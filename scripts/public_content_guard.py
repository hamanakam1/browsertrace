#!/usr/bin/env python3
"""Inspect Git objects as data. Never import or execute candidate-tree code."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
import urllib.request

SECRET = "PUBLIC_CONTENT_BLOCKLIST_JSON"
CONTEXT = "public-content-privacy"
MAX_FILES = 20000
MAX_BLOB = 16 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
CONTROL_PREFIXES = (b".github/",)
CONTROL_FILES = (b"scripts/public_content_guard.py", b"tests/test_public_content_guard.py")


class GuardError(Exception):
    """Only fixed, public-safe error messages belong here."""


def normalized(value):
    return unicodedata.normalize("NFKC", value).casefold()


def load_terms(raw):
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        raise GuardError("Private blocklist is missing or invalid.") from None
    if (not isinstance(values, list) or not 1 <= len(values) <= 256
            or any(not isinstance(v, str) or not 3 <= len(v) <= 512
                   or not v.strip() or any(ord(c) < 32 for c in v) for v in values)):
        raise GuardError("Private blocklist is missing or invalid.")
    return tuple(normalized(v) for v in values)


def matches(data, terms):
    # No regexes supplied through the secret. UTF-16 covers common text exports.
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        text = normalized(data.decode(encoding, errors="ignore"))
        if any(term in text for term in terms):
            return True
    return False


def safe_path(path, terms):
    if matches(path, terms):
        return "[path withheld]"
    # Escape newlines, terminal controls and workflow-command delimiters as data.
    return json.dumps(path.decode("utf-8", errors="replace"), ensure_ascii=True)


def git(root, *args, limit=MAX_TOTAL):
    # Do not pass the blocklist, API token, injected Git settings or local hooks.
    env = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=",
             "-c", "protocol.file.allow=never", "-C", str(root), *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
            check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GuardError("Git object operation failed.") from None
    if result.returncode or len(result.stdout) > limit:
        raise GuardError("Git object operation failed or exceeded a limit.")
    return result.stdout


def valid_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise GuardError("Invalid commit identity.")
    return value


def tree(root, revision):
    valid_sha(revision)
    records = git(root, "ls-tree", "-rlz", revision, limit=8 * 1024 * 1024).split(b"\0")
    result = {}
    total = 0
    for record in filter(None, records):
        metadata, path = record.split(b"\t", 1)
        mode, kind, oid, size = metadata.split()
        if kind != b"blob" or mode not in (b"100644", b"100755", b"120000"):
            raise GuardError("Unsupported Git object; coverage is incomplete.")
        length = int(size)
        total += length
        if length > MAX_BLOB or total > MAX_TOTAL or len(result) >= MAX_FILES:
            raise GuardError("Repository scan limit exceeded; coverage is incomplete.")
        result[path] = (oid.decode("ascii"), length)
    if not result:
        raise GuardError("Empty candidate tree; coverage is incomplete.")
    return result


def scan(root, revision, terms, base=None, reviewed_controls=False):
    entries = tree(root, revision)
    if base and not reviewed_controls:
        before = tree(root, base)
        for path in entries.keys() | before.keys():
            if ((path in CONTROL_FILES or path.startswith(CONTROL_PREFIXES))
                    and entries.get(path) != before.get(path)):
                raise GuardError("Workflow or guard changes require a maintainer-reviewed manual scan.")
    bad = []
    for path, (oid, size) in entries.items():
        data = git(root, "cat-file", "blob", oid, limit=MAX_BLOB)
        if len(data) != size:
            raise GuardError("Git object size mismatch; coverage is incomplete.")
        if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise GuardError("External large-file content is not covered.")
        if matches(path, terms) or matches(data, terms):
            bad.append(safe_path(path, terms))
    if bad:
        for path in bad[:50]:
            print("Blocked content in file: " + path)
        raise GuardError("Private-content match found. Terms and excerpts are withheld.")
    print(f"Public-content scan passed: {len(entries)} tracked files.")


def repository():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise GuardError("Invalid repository identity.")
    return repo


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path, payload=None):
    token = os.environ.get("GUARD_STATUS_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "public-content-guard",
               "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request("https://api.github.com/repos/" + repository() + path,
                                     data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError()
        return json.loads(body)
    except Exception:
        raise GuardError("GitHub API operation failed; no successful status was issued.") from None


def prepare(root, event, event_name, current_sha):
    """Fetch candidates into the object database, not the working tree."""
    repo = repository()
    base = valid_sha(current_sha)
    reviewed = False
    number = None
    if event_name == "pull_request_target":
        pr = event["pull_request"]
        if pr["base"]["repo"]["full_name"] != repo or pr["base"]["ref"] != "main":
            raise GuardError("Unexpected pull-request target.")
        number = pr["number"]
        head = valid_sha(pr["head"]["sha"])
        base = valid_sha(pr["base"]["sha"])
    elif event_name == "workflow_dispatch":
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise GuardError("Manual scans must use the main branch workflow.")
        inputs = event.get("inputs") or {}
        supplied = str(inputs.get("pull_request", "")).strip()
        if supplied:
            if not re.fullmatch(r"[1-9][0-9]{0,8}", supplied):
                raise GuardError("Invalid pull-request number.")
            number = int(supplied)
            pr = api(f"/pulls/{number}")
            if (pr["state"] != "open" or pr["base"]["ref"] != "main"
                    or pr["base"]["repo"]["full_name"] != repo):
                raise GuardError("Unexpected pull-request target.")
            head = valid_sha(pr["head"]["sha"])
            # Manual reviews apply to this exact head and current trusted main.
            reviewed = inputs.get("reviewed_control_changes") in (True, "true")
        else:
            head = base
    elif event_name == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main":
        head = base
    else:
        raise GuardError("Unsupported event or branch.")
    if number is not None:
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 999999999:
            raise GuardError("Invalid pull-request number.")
        git(root, "fetch", "--no-tags", "--depth=1", "https://github.com/" + repo + ".git",
            f"refs/pull/{number}/head")
        if git(root, "rev-parse", "FETCH_HEAD").decode().strip() != head:
            raise GuardError("Pull request changed during inspection; run again.")
    return {"head": head, "base": base if number else "", "reviewed": reviewed}


def status(state, sha):
    valid_sha(sha)
    if not os.environ.get("GUARD_STATUS_TOKEN"):
        raise GuardError("Status credential is unavailable.")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id.isdigit():
        raise GuardError("Invalid run identity.")
    api("/statuses/" + sha, {
        "state": state, "context": CONTEXT,
        "description": {"pending": "Privacy scan is running.", "success": "Tracked content passed privacy policy.",
                        "failure": "Privacy scan failed or could not complete."}[state],
        "target_url": "https://github.com/" + repository() + "/actions/runs/" + run_id,
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "scan", "status"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--revision")
    parser.add_argument("--base")
    parser.add_argument("--reviewed-controls", action="store_true")
    parser.add_argument("--state", choices=("pending", "success", "failure"))
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
            context = prepare(args.root, event, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"])
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                for key, value in context.items():
                    output.write(f"{key}={str(value).lower()}\n")
        elif args.command == "scan":
            terms = load_terms(os.environ.pop(SECRET, ""))
            scan(args.root, args.revision, terms, args.base, args.reviewed_controls)
        else:
            status(args.state, args.revision)
    except GuardError as error:
        print("Privacy check failed: " + str(error), file=sys.stderr)
        return 1
    except Exception:
        # Never include exception details: they can contain candidate data or secrets.
        print("Privacy check failed: inspection could not complete.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
