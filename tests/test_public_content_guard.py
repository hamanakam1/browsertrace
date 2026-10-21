"""Synthetic fixtures only: private deployment identifiers do not belong here."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/public_content_guard.py"
spec = importlib.util.spec_from_file_location("public_content_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class MatchingTests(unittest.TestCase):
    def test_literal_case_insensitive_and_unicode_normalized(self):
        terms = guard.load_terms('["fictional-violet", "a.b"]')
        self.assertTrue(guard.matches(b"FICTIONAL-VIOLET", terms))
        self.assertTrue(guard.matches("ｆｉｃｔｉｏｎａｌ-violet".encode(), terms))
        self.assertTrue(guard.matches(b"a.b", terms))
        self.assertFalse(guard.matches(b"aXb", terms))

    def test_utf16_both_byte_orders(self):
        for encoding in ("utf-16-le", "utf-16-be"):
            self.assertTrue(guard.matches("fictional-violet".encode(encoding), ("fictional-violet",)))

    def test_invalid_configuration_fails_without_echo(self):
        for raw in ("", "fictional-violet", "null", "{}", "[]", '[1]', '[""]',
                    '["ab"]', '["  "]', '["bad\\nvalue"]', '["' + "x" * 513 + '"]'):
            with self.subTest(raw=raw), self.assertRaises(guard.GuardError) as caught:
                guard.load_terms(raw)
            self.assertNotIn("fictional-violet", str(caught.exception))
        with self.assertRaises(guard.GuardError):
            guard.load_terms(json.dumps(["abc"] * 257))

    def test_paths_with_terms_are_withheld(self):
        self.assertEqual(guard.safe_path(b"fictional-violet/report.md", ("fictional-violet",)),
                         "[path withheld]")

    def test_path_log_injection_is_escaped(self):
        display = guard.safe_path(b"note\n::error::fake\x1b.md", ("fictional-violet",))
        self.assertNotIn("\n", display)
        self.assertNotIn("\x1b", display)
        self.assertTrue(display.startswith('"'))

    def test_sha_must_be_exact(self):
        for value in ("main", "-h", "a" * 39, "a" * 41, "A" * 40, None):
            with self.assertRaises(guard.GuardError):
                guard.valid_sha(value)

    def test_git_subprocess_does_not_inherit_secrets(self):
        with mock.patch.dict(os.environ, {guard.SECRET: '["fictional-violet"]',
                                         "GUARD_STATUS_TOKEN": "fake-api-token",
                                         "GIT_CONFIG_COUNT": "9"}):
            with mock.patch.object(guard.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, b"ok", b"")
                guard.git(".", "rev-parse", "HEAD")
                env = run.call_args.kwargs["env"]
                self.assertNotIn(guard.SECRET, env)
                self.assertNotIn("GUARD_STATUS_TOKEN", env)
                self.assertNotIn("GIT_CONFIG_COUNT", env)

    def test_unknown_exception_is_redacted(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {guard.SECRET: '["fictional-violet"]'}), \
                mock.patch.object(guard, "scan", side_effect=ValueError("fictional-violet")), \
                contextlib.redirect_stderr(out):
            self.assertEqual(guard.main(["scan", "--revision", "a" * 40]), 1)
        self.assertNotIn("fictional-violet", out.getvalue())


class ObjectScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_git("init", "-q")
        self.write("readme.txt", b"An ordinary public example.")
        self.base = self.commit()
        self.terms = ("fictional-violet",)

    def run_git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *args],
            stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def commit(self):
        # Fixture-only temporary repository, never an operator checkout.
        self.run_git("add", "--", *[str(p.relative_to(self.root)) for p in self.root.rglob("*")
                                   if p.is_file() and ".git" not in p.relative_to(self.root).parts])
        self.run_git("commit", "-qm", "synthetic fixture")
        return self.run_git("rev-parse", "HEAD")

    def scan(self, sha, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            guard.scan(self.root, sha, self.terms, **kwargs)

    def test_clean_tree_passes(self):
        self.scan(self.base)

    def test_file_content_match_fails_and_withholds_term(self):
        self.write("example.txt", b"FICTIONAL-VIOLET private example")
        sha = self.commit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(guard.GuardError):
            guard.scan(self.root, sha, self.terms)
        self.assertIn("example.txt", out.getvalue())
        self.assertNotIn("fictional-violet", out.getvalue().lower())
        self.assertNotIn("private example", out.getvalue())

    def test_path_match_fails_and_withholds_path(self):
        self.write("fictional-violet.txt", b"ordinary")
        sha = self.commit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(guard.GuardError):
            guard.scan(self.root, sha, self.terms)
        self.assertIn("[path withheld]", out.getvalue())
        self.assertNotIn("fictional-violet", out.getvalue())

    def test_symlink_is_scanned_as_data_not_followed(self):
        (self.root / "link").symlink_to("/fictional-violet/nonexistent")
        sha = self.commit_symlink()
        with self.assertRaises(guard.GuardError):
            self.scan(sha)

    def commit_symlink(self):
        self.run_git("add", "--", "link")
        self.run_git("commit", "-qm", "synthetic symlink")
        return self.run_git("rev-parse", "HEAD")

    def test_untracked_working_tree_is_not_scanned(self):
        self.write("local-only.txt", b"fictional-violet")
        self.scan(self.base)

    def test_history_is_not_scanned(self):
        self.write("readme.txt", b"fictional-violet")
        self.commit()
        self.write("readme.txt", b"public again")
        self.scan(self.commit())

    def test_candidate_python_is_never_executed(self):
        self.write("sitecustomize.py", b'raise RuntimeError("must never execute")')
        self.write("scripts/public_content_guard.py", b'raise RuntimeError("must never execute")')
        self.write("tests/test_public_content_guard.py", b'raise RuntimeError("must never execute")')
        sha = self.commit()
        self.scan(sha, base=self.base, reviewed_controls=True)
        with self.assertRaisesRegex(guard.GuardError, "maintainer-reviewed"):
            self.scan(sha, base=self.base)

    def test_workflow_addition_requires_review(self):
        self.write(".github/workflows/example.yml", b"name: synthetic")
        sha = self.commit()
        with self.assertRaisesRegex(guard.GuardError, "maintainer-reviewed"):
            self.scan(sha, base=self.base)
        self.scan(sha, base=self.base, reviewed_controls=True)

    def test_review_does_not_bypass_content_matching(self):
        self.write(".github/workflows/example.yml", b"# fictional-violet")
        sha = self.commit()
        with self.assertRaises(guard.GuardError):
            self.scan(sha, base=self.base, reviewed_controls=True)

    def test_deleted_control_file_requires_review(self):
        self.write(".github/example", b"ordinary")
        before = self.commit()
        self.run_git("rm", "--", ".github/example")
        self.run_git("commit", "-qm", "synthetic removal")
        with self.assertRaisesRegex(guard.GuardError, "maintainer-reviewed"):
            self.scan(self.run_git("rev-parse", "HEAD"), base=before)

    def test_lfs_pointer_fails_closed(self):
        self.write("external.txt", b"version https://git-lfs.github.com/spec/v1\noid fake")
        with self.assertRaisesRegex(guard.GuardError, "External large-file"):
            self.scan(self.commit())

    def test_submodule_fails_closed(self):
        self.run_git("update-index", "--add", "--cacheinfo", "160000," + self.base + ",external")
        self.run_git("commit", "-qm", "synthetic gitlink")
        with self.assertRaisesRegex(guard.GuardError, "Unsupported Git object"):
            self.scan(self.run_git("rev-parse", "HEAD"))

    def test_size_file_count_and_total_limits_fail(self):
        for name in ("MAX_BLOB", "MAX_TOTAL", "MAX_FILES"):
            with mock.patch.object(guard, name, 0), self.assertRaises(guard.GuardError):
                self.scan(self.base)

    def test_cli_missing_secret_fails_closed_without_traceback(self):
        env = {k: v for k, v in os.environ.items() if k != guard.SECRET}
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "scan", "--root",
                               str(self.root), "--revision", self.base],
                              env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("missing or invalid", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "example/project", "GITHUB_REF": "refs/heads/main"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def pr(self):
        return {"number": 12, "base": {"sha": "a" * 40, "ref": "main",
                                      "repo": {"full_name": "example/project"}},
                "head": {"sha": "b" * 40}, "state": "open"}

    def test_candidate_fetch_race_fails(self):
        with mock.patch.object(guard, "git", side_effect=[b"", b"c" * 40]), \
                self.assertRaisesRegex(guard.GuardError, "changed during"):
            guard.prepare(".", {"pull_request": self.pr()}, "pull_request_target", "a" * 40)

    def test_pr_resolves_exact_sha_without_checkout(self):
        with mock.patch.object(guard, "git", side_effect=[b"", b"b" * 40]) as git:
            context = guard.prepare(".", {"pull_request": self.pr()}, "pull_request_target", "a" * 40)
        self.assertEqual(context, {"head": "b" * 40, "base": "a" * 40, "reviewed": False})
        self.assertNotIn("checkout", str(git.call_args_list))
        self.assertIn("refs/pull/12/head", str(git.call_args_list))

    def test_manual_review_uses_main_and_exact_pr(self):
        event = {"inputs": {"pull_request": "12", "reviewed_control_changes": "true"}}
        with mock.patch.object(guard, "api", return_value=self.pr()), \
                mock.patch.object(guard, "git", side_effect=[b"", b"b" * 40]):
            result = guard.prepare(".", event, "workflow_dispatch", "a" * 40)
        self.assertTrue(result["reviewed"])
        with mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/other"}), \
                self.assertRaises(guard.GuardError):
            guard.prepare(".", event, "workflow_dispatch", "a" * 40)

    def test_untrusted_event_is_rejected(self):
        with self.assertRaises(guard.GuardError):
            guard.prepare(".", {}, "pull_request", "a" * 40)

    def test_public_status_contains_only_fixed_metadata(self):
        with mock.patch.dict(os.environ, {"GUARD_STATUS_TOKEN": "fake-token", "GITHUB_RUN_ID": "123"}), \
                mock.patch.object(guard, "api") as api:
            guard.status("failure", "b" * 40)
        payload = api.call_args.args[1]
        self.assertEqual(payload["context"], "public-content-privacy")
        self.assertNotIn("fake-token", json.dumps(payload))
        self.assertEqual(api.call_args.args[0], "/statuses/" + "b" * 40)


if __name__ == "__main__":
    unittest.main()
