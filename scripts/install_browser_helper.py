#!/usr/bin/env python3
"""Register an on-demand native messaging host, never a login/background service."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
from urllib.parse import urlsplit

HOST_NAME = "com.panther.intent_capture"
ROOT = Path(__file__).resolve().parents[1]


def absolute_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an absolute path")
    return path.resolve()


def browser_directory(browser, platform, home):
    if platform == "darwin":
        names = {"edge": "Microsoft Edge", "chrome": "Google/Chrome", "chromium": "Chromium"}
        return home / "Library/Application Support" / names[browser]
    if platform.startswith("linux"):
        names = {"edge": "microsoft-edge", "chrome": "google-chrome", "chromium": "chromium"}
        return home / ".config" / names[browser]
    raise ValueError("The browser helper installer currently supports macOS and Linux only")


def local_url(value, *, inference=False):
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid local service URL")
    url = urlsplit(value)
    try:
        local = url.hostname == "localhost" or ipaddress.ip_address(url.hostname or "").is_loopback
    except ValueError:
        local = False
    allowed_schemes = {"http"} if inference else {"http", "https"}
    if (not local or "%" in (url.hostname or "") or url.scheme not in allowed_schemes
            or url.username is not None or url.password is not None
            or url.query or url.fragment or (not inference and url.path not in ("", "/"))):
        raise ValueError("Use a loopback URL without credentials, query, or fragment")
    _ = url.port  # Validate the port before writing anything.
    return value.rstrip("/")


def _json(data):
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _no_symlinks(path):
    for entry in (path, *path.parents):
        if entry.is_symlink():
            raise ValueError(f"Refusing a symlink in the installation path: {entry}")


def plan_install(*, browser, extension_id, data_dir, dashboard_url="http://127.0.0.1:7878",
                 user_data_dir=None, ollama_endpoint=None, ollama_model=None,
                 home=None, platform=None, root=ROOT, python=None):
    platform = platform or sys.platform
    home = absolute_path(home or Path.home())
    if browser not in {"edge", "chrome", "chromium"}:
        raise ValueError("Choose edge, chrome, or chromium")
    default_dir = browser_directory(browser, platform, home)
    if not isinstance(extension_id, str) or not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("Extension ID must be exactly 32 lowercase letters a through p")
    data_dir = absolute_path(data_dir)
    user_data_dir = absolute_path(user_data_dir) if user_data_dir else default_dir
    root = absolute_path(root)
    executable = absolute_path(python or Path(sys.executable).resolve())
    host = root / "backend/native_host.py"
    if not host.is_file() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("The checkout's native host and an executable Python are required")
    helper_dir = data_dir / "browser-helper" / browser / extension_id
    config_path = helper_dir / "config.json"
    launcher = helper_dir / "launch.sh"
    manifest = user_data_dir / "NativeMessagingHosts" / (HOST_NAME + ".json")
    origin = f"chrome-extension://{extension_id}/"
    config = {"version": 1, "allowed_origin": origin, "data_dir": str(data_dir),
              "dashboard_url": local_url(dashboard_url)}
    if ollama_endpoint is not None:
        config["ollama_endpoint"] = local_url(ollama_endpoint, inference=True)
    if ollama_model is not None:
        if not ollama_model.strip() or len(ollama_model) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in ollama_model):
            raise ValueError("Supply a nonempty local model name of at most 200 characters")
        config["ollama_model"] = ollama_model
    command = shlex.quote(str(executable)) + " -I -B " + shlex.quote(str(host))
    wrapper = f'#!/bin/sh\nexec {command} --config {shlex.quote(str(config_path))} "$@"\n'
    files = {
        config_path: (_json(config), 0o600),
        launcher: (wrapper.encode(), 0o700),
        manifest: (_json({"name": HOST_NAME, "description": "Panther browser-controlled local backend",
                         "path": str(launcher), "type": "stdio", "allowed_origins": [origin]}), 0o600),
    }
    receipt_path = helper_dir / "install-receipt.json"
    receipt = {"version": 1, "browser": browser, "extension_id": extension_id,
               "files": {str(path): _digest(content) for path, (content, _) in files.items()}}
    files[receipt_path] = (_json(receipt), 0o600)
    return {"files": files, "manifest": manifest, "receipt": receipt_path, "helper_dir": helper_dir}


def install(plan):
    """Preflight every target. Do not replace another host or edited configuration."""
    files = plan["files"]
    for path, (content, mode) in files.items():
        _no_symlinks(path)
        if path.exists() and (not path.is_file() or path.read_bytes() != content):
            raise ValueError(f"Existing file differs; refusing to overwrite it: {path}. Uninstall the old registration first.")
        if path.exists() and (path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != mode):
            raise ValueError(f"Existing helper file has unexpected ownership or permissions: {path}. Restore private permissions or uninstall the registration first.")
    created = []
    try:
        for path, (content, mode) in files.items():
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Exclusive creation avoids replacing a file written after preflight.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            created.append(path)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return plan["manifest"]


def uninstall(*, browser, extension_id, data_dir, user_data_dir=None, home=None, platform=None):
    """Remove only exact, unchanged registered files; leave captures and directories."""
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("Invalid extension ID")
    if browser not in {"edge", "chrome", "chromium"}:
        raise ValueError("Invalid browser")
    profile = browser_directory(browser, platform or sys.platform, absolute_path(home or Path.home()))
    if user_data_dir:
        profile = absolute_path(user_data_dir)
    helper = absolute_path(data_dir) / "browser-helper" / browser / extension_id
    manifest = profile / "NativeMessagingHosts" / (HOST_NAME + ".json")
    receipt_path = helper / "install-receipt.json"
    expected = {manifest, helper / "config.json", helper / "launch.sh"}
    for path in (*expected, receipt_path):
        _no_symlinks(path)
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get("version") != 1 or receipt.get("browser") != browser
            or receipt.get("extension_id") != extension_id or set(receipt.get("files", {})) != {str(p) for p in expected}):
        raise ValueError("Registration receipt does not match the exact installation targets")
    for path in expected:
        if not path.is_file() or _digest(path.read_bytes()) != receipt["files"][str(path)]:
            raise ValueError(f"Registration was changed; refusing removal: {path}")
    for path in (manifest, helper / "launch.sh", helper / "config.json", receipt_path):
        path.unlink()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("edge", "chrome", "chromium"), required=True)
    parser.add_argument("--extension-id", required=True, help="Copy the ID from the browser's extensions page")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".panther-intent-capture")
    parser.add_argument("--user-data-dir", type=Path, help="Explicit custom browser user-data directory, not its Default subfolder")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:7878")
    parser.add_argument("--ollama-endpoint")
    parser.add_argument("--ollama-model")
    parser.add_argument("--uninstall", action="store_true", help="Remove unchanged helper registration, not saved captures")
    args = parser.parse_args()
    values = vars(args).copy()
    removing = values.pop("uninstall")
    try:
        if removing:
            for key in ("dashboard_url", "ollama_endpoint", "ollama_model"):
                values.pop(key)
            path = uninstall(**values)
            print(f"Removed Panther helper registration: {path}\nSaved captures and receipts remain. Re-run installation to restore browser control.")
        else:
            path = install(plan_install(**values))
            print(f"Registered on-demand Panther helper: {path}\nNo service was started and no login item was installed. Reload Panther, then use Turn Panther on in its popup.")
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, f"Panther helper setup failed: {error}\n")


if __name__ == "__main__":
    main()
