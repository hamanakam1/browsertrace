#!/usr/bin/env python3
"""Browser-owned native messaging supervisor (macOS/Linux, no login service).

The installer supplies a private config and the browser supplies its own origin.
Messages never accept executable paths, shell commands, environment, or targets.
Only this supervisor's child can be stopped; an existing listener is not adopted.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import stat
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit

MAX_FRAME = 4096
MAX_CONFIG = 16384
BACKEND_PORT = 7890
START_TIMEOUT = 10
STOP_TIMEOUT = 23
_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}/\Z")
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class ProtocolError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON keys are not allowed")
        result[key] = value
    return result


def _read_exact(stream, count):
    pieces = []
    remaining = count
    while remaining:
        part = stream.read(remaining)
        if not part:
            if remaining == count:
                return None
            raise ProtocolError("Truncated native message")
        pieces.append(part)
        remaining -= len(part)
    return b"".join(pieces)


def read_message(stream):
    header = _read_exact(stream, 4)
    if header is None:
        return None
    length = struct.unpack("=I", header)[0]
    if not 1 <= length <= MAX_FRAME:
        raise ProtocolError("Native message exceeds its bounded size")
    body = _read_exact(stream, length)
    if body is None:
        raise ProtocolError("Truncated native message")
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=_object)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Native message must be valid UTF-8 JSON") from exc


def write_message(stream, value):
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_FRAME:
        raise ProtocolError("Native response exceeds its bounded size")
    stream.write(struct.pack("=I", len(raw)) + raw)
    stream.flush()


def _local_url(value, *, inference=False):
    if (not isinstance(value, str) or len(value) > 1000
            or any(ord(char) < 33 or ord(char) == 127 for char in value)):
        raise ValueError("Invalid local service URL")
    parsed = urlsplit(value)
    host = parsed.hostname
    if (parsed.scheme not in ({"http"} if inference else {"http", "https"})
            or not host or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or (not inference and parsed.path not in {"", "/"})):
        raise ValueError("Configured services must be credential-free loopback URLs")
    host = "127.0.0.1" if host.lower() == "localhost" else host
    if "%" in host or not ipaddress.ip_address(host).is_loopback:
        raise ValueError("Configured services must use loopback addresses")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{host}:{port}" + (parsed.path.rstrip("/") if inference else "")


def load_config(path, origin):
    """Read only an installer-selected, owner-private regular configuration file."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Native host config path must be absolute")
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > MAX_CONFIG):
            raise ValueError("Native host config must be an owner-private regular file")
        value = json.loads(handle.read(MAX_CONFIG + 1).decode("utf-8"), object_pairs_hook=_object)
    allowed = {"version", "allowed_origin", "data_dir", "dashboard_url", "ollama_endpoint", "ollama_model"}
    if (not isinstance(value, dict) or set(value) - allowed or type(value.get("version")) is not int
            or value["version"] != 1 or not isinstance(value.get("allowed_origin"), str)
            or not _ORIGIN.fullmatch(value["allowed_origin"]) or origin != value["allowed_origin"]):
        raise ValueError("Native host origin or configuration is not authorized")
    directory = value.get("data_dir")
    if not isinstance(directory, str) or not directory or not Path(directory).is_absolute():
        raise ValueError("Native capture directory must be absolute")
    result = dict(value, data_dir=str(Path(directory).resolve()),
                  dashboard_url=_local_url(value["dashboard_url"]) if "dashboard_url" in value else "")
    if "ollama_endpoint" in value:
        result["ollama_endpoint"] = _local_url(value["ollama_endpoint"], inference=True)
    if "ollama_model" in value and (not isinstance(value["ollama_model"], str)
            or not 1 <= len(value["ollama_model"]) <= 200 or any(ord(c) < 32 or ord(c) == 127 for c in value["ollama_model"])):
        raise ValueError("Invalid local model name")
    return result


class BackendOwner:
    """Own one direct child and its inherited liveness pipe, never a looked-up PID."""
    def __init__(self, config, *, port=BACKEND_PORT):
        self.config = config
        self.port = port  # Test injection; browser/config cannot choose a port.
        self.child = None
        self.lease = None

    def _environment(self):
        env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
        env.update(PYTHONUNBUFFERED="1", PANTHER_DATA_DIR=self.config["data_dir"],
                   PANTHER_DASHBOARD_URL=self.config["dashboard_url"], EXTENSION_BACKEND_PORT=str(self.port))
        if self.config.get("ollama_endpoint"):
            env["OLLAMA_ENDPOINT"] = self.config["ollama_endpoint"]
        if self.config.get("ollama_model"):
            env["OLLAMA_MODEL"] = self.config["ollama_model"]
        return env

    def status(self):
        if self.child is None:
            return {"ok": True, "state": "off"}
        if self.child.poll() is not None:
            self._close_lease()
            return {"ok": False, "state": "error", "error": "The browser-owned backend exited; choose On explicitly to start again"}
        return {"ok": True, "state": "on"}

    def _close_lease(self):
        if self.lease is not None:
            os.close(self.lease)
            self.lease = None

    def _force_stop(self):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=1)

    def start(self):
        if self.child is not None and self.child.poll() is None:
            return {"ok": True, "state": "on"}
        self._close_lease()
        parent_read, self.lease = os.pipe()
        ready_read, ready_write = os.pipe()
        server = Path(__file__).resolve().with_name("server.py")
        try:
            self.child = subprocess.Popen(
                [sys.executable, "-I", "-B", str(server), "--native-parent-fd", str(parent_read),
                 "--native-ready-fd", str(ready_write)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                close_fds=True, pass_fds=(parent_read, ready_write), env=self._environment(),
                cwd=str(server.parent),
            )
        except OSError:
            self._close_lease()
            os.close(ready_read)
            raise
        finally:
            os.close(parent_read)
            os.close(ready_write)
        deadline = time.monotonic() + START_TIMEOUT
        data = b""
        try:
            while time.monotonic() < deadline and len(data) <= MAX_FRAME:
                readable, _, _ = select.select([ready_read], [], [], max(0, deadline - time.monotonic()))
                if not readable:
                    break
                part = os.read(ready_read, MAX_FRAME + 1)
                if not part:
                    break
                data += part
                if b"\n" in data:
                    result = json.loads(data.split(b"\n", 1)[0])
                    if result.get("ok") is True and self.child.poll() is None:
                        return {"ok": True, "state": "on"}
                    return self._failed_start(str(result.get("error") or "Backend could not start"))
            return self._failed_start("Browser-owned backend did not confirm startup")
        except (ValueError, OSError):
            return self._failed_start("Browser-owned backend returned invalid startup confirmation")
        finally:
            os.close(ready_read)

    def _failed_start(self, error):
        self._close_lease()
        self._force_stop()
        self.child = None
        return {"ok": False, "state": "error", "error": error[:400]}

    def stop(self):
        self._close_lease()  # Child EOF watchdog closes admission before cleanup.
        if self.child is not None:
            try:
                self.child.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._force_stop()
            if self.child.poll() is None:
                return {"ok": False, "state": "error", "error": "Could not confirm the owned backend stopped"}
        self.child = None
        return {"ok": True, "state": "off"}


def serve(owner, source, destination):
    try:
        while True:
            message = read_message(source)
            if message is None:
                break
            request_id = message.get("id") if isinstance(message, dict) else None
            if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
                raise ProtocolError("Native command requires a bounded request id")
            if (set(message) != {"id", "action"} or not isinstance(message.get("action"), str)
                    or message["action"] not in {"start", "status", "stop"}):
                result = {"ok": False, "state": "error", "error": "Only start, status, or stop without extra fields is permitted"}
            else:
                try:
                    result = getattr(owner, message["action"])()
                except (OSError, ValueError, subprocess.SubprocessError):
                    result = {"ok": False, "state": "error", "error": "Native backend operation failed; check the local browser log"}
            write_message(destination, {"id": request_id, **result})
    finally:
        owner.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Browser-owned Panther native host")
    parser.add_argument("--config", required=True)
    parser.add_argument("origin")
    args = parser.parse_args(argv)
    if os.name != "posix":
        parser.error("This native helper currently supports macOS and Linux only")
    try:
        config = load_config(args.config, args.origin)
        serve(BackendOwner(config), sys.stdin.buffer, sys.stdout.buffer)
    except (OSError, ValueError, ProtocolError):
        print("Panther native host closed: invalid configuration, origin, or protocol", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
