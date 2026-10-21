import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_browser_helper", ROOT / "scripts/install_browser_helper.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="panther-install-")
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / "checkout with spaces"
        (self.root / "backend").mkdir(parents=True)
        (self.root / "backend/native_host.py").write_text("# fixture host\n")
        self.args = dict(browser="edge", extension_id="a" * 32, data_dir=self.home / "private",
                         home=self.home, platform="darwin", root=self.root, python=Path(sys.executable).resolve())

    def tearDown(self):
        self.temp.cleanup()

    def test_on_demand_exact_origin_private_files_and_idempotence(self):
        plan = installer.plan_install(**self.args)
        path = installer.install(plan)
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["allowed_origins"], ["chrome-extension://" + "a" * 32 + "/"])
        self.assertEqual(manifest["name"], "com.panther.intent_capture")
        launcher = Path(manifest["path"])
        self.assertIn("--config", launcher.read_text())
        self.assertIn('"$@"', launcher.read_text())
        self.assertIn("'", launcher.read_text())
        self.assertNotIn("launchctl", launcher.read_text())
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        before = {p: p.stat().st_mtime_ns for p in plan["files"]}
        installer.install(plan)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in plan["files"]})

    def test_custom_profile_and_linux_paths(self):
        for browser, folder in (("edge", "microsoft-edge"), ("chrome", "google-chrome"), ("chromium", "chromium")):
            path = installer.browser_directory(browser, "linux", self.home)
            self.assertEqual(path, self.home / ".config" / folder)
        profile = self.home / "isolated-profile"
        plan = installer.plan_install(**self.args, user_data_dir=profile)
        self.assertEqual(plan["manifest"].parent, profile / "NativeMessagingHosts")

    def test_invalid_ids_remote_urls_and_platforms_create_nothing(self):
        variants = ({"extension_id": "../../bad"}, {"extension_id": "z" * 32},
                    {"dashboard_url": "https://example.com"}, {"dashboard_url": "http://user@127.0.0.1:7878"},
                    {"dashboard_url": "http://@127.0.0.1:7878"}, {"dashboard_url": "http://[::1%25lo0]:7878"},
                    {"ollama_model": "model\tname"},
                    {"ollama_endpoint": "https://127.0.0.1:11434/v1"}, {"platform": "win32"},
                    {"data_dir": Path("relative")})
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                installer.plan_install(**{**self.args, **variant})
        self.assertFalse(self.args["data_dir"].exists())

    def test_different_registration_is_not_overwritten(self):
        plan = installer.plan_install(**self.args)
        installer.install(plan)
        manifest = plan["manifest"].read_bytes()
        other = installer.plan_install(**{**self.args, "extension_id": "b" * 32})
        with self.assertRaises(ValueError):
            installer.install(other)
        self.assertEqual(plan["manifest"].read_bytes(), manifest)
        self.assertFalse(other["helper_dir"].exists())

    def test_symlink_is_refused(self):
        plan = installer.plan_install(**self.args)
        plan["manifest"].parent.mkdir(parents=True)
        outside = self.home / "unrelated.json"
        outside.write_text("preserve")
        plan["manifest"].symlink_to(outside)
        with self.assertRaises(ValueError):
            installer.install(plan)
        self.assertEqual(outside.read_text(), "preserve")

    def test_identical_files_with_unsafe_or_nonexecutable_modes_are_not_success(self):
        plan = installer.plan_install(**self.args)
        installer.install(plan)
        for name, bad_mode, good_mode in (("config.json", 0o644, 0o600), ("launch.sh", 0o600, 0o700)):
            with self.subTest(name=name):
                path = plan["helper_dir"] / name
                path.chmod(bad_mode)
                with self.assertRaisesRegex(ValueError, "permissions"):
                    installer.install(plan)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), bad_mode)
                path.chmod(good_mode)

    def test_uninstall_preserves_capture_and_modified_registration(self):
        plan = installer.plan_install(**self.args)
        installer.install(plan)
        capture = self.args["data_dir"] / "evidence.json"
        capture.write_text("private evidence")
        args = {k: v for k, v in self.args.items() if k not in ("root", "python")}
        original = plan["manifest"].read_bytes()
        plan["manifest"].write_bytes(original + b"\n")
        with self.assertRaises(ValueError):
            installer.uninstall(**args)
        plan["manifest"].write_bytes(original)
        installer.uninstall(**args)
        self.assertTrue(capture.exists())
        self.assertTrue(all(not p.exists() for p in plan["files"]))


if __name__ == "__main__":
    unittest.main()
