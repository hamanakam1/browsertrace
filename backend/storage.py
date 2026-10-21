"""Private, atomic file storage for Panther captures and derived artifacts."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from redaction import sanitize_url

ROOT = Path(os.environ.get("PANTHER_DATA_DIR", str(Path.home() / ".panther-intent-capture"))).expanduser()
RECORDINGS_DIR = ROOT / "recordings"
EVENTS_DIR = ROOT / "events"
INFERENCE_DIR = ROOT / "inference"
TICKETS_DIR = ROOT / "tickets"
CONSENT_LOG = ROOT / "consent.log"
MANIFEST_FILE = ROOT / "manifest.json"
MAX_RECORDINGS = 200
TTL_SECONDS = 30 * 24 * 60 * 60
DATA_LOCK = threading.RLock()


def ensure_dirs() -> None:
    for directory in (ROOT, RECORDINGS_DIR, EVENTS_DIR, INFERENCE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass


def _safe_id(recording_id: str) -> str:
    if not isinstance(recording_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", recording_id):
        raise ValueError("Invalid recording id")
    return recording_id


def recording_id_for_session(extension_id: str, session_id: str) -> str:
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 160:
        raise ValueError("Invalid session id")
    if not isinstance(extension_id, str) or not 1 <= len(extension_id) <= 160:
        raise ValueError("Invalid extension id")
    identity = json.dumps([extension_id, session_id], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def write_json(path: Path, payload: Any) -> None:
    """Replace a JSON artifact atomically, with owner-only permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=".panther-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _owned_path(value: Any) -> Optional[Path]:
    if not value:
        return None
    path = Path(value).resolve()
    root = ROOT.resolve()
    if path == root or root not in path.parents:
        raise ValueError("Capture artifact must be inside PANTHER_DATA_DIR")
    return path


def create_recording(
    *, tab_id: int, start_time: float, end_time: float, event_count: int,
    screenshot_count: int = 0, extension_id: str = "unknown",
    video_path: Optional[Path] = None, events_path: Optional[Path] = None,
    screenshots_path: Optional[Path] = None, video_size: int = 0,
    recording_id: Optional[str] = None, session_id: str = "", url: str = "",
    title: str = "", goal: str = "", metadata: Optional[Dict[str, Any]] = None,
) -> str:
    if not all(math.isfinite(value) for value in (start_time, end_time)) or end_time < start_time:
        raise ValueError("Invalid recording times")
    derived_id = recording_id_for_session(extension_id, session_id) if session_id else None
    if recording_id and derived_id and recording_id != derived_id:
        raise ValueError("Recording id does not match session")
    recording_id = _safe_id(recording_id or derived_id or uuid.uuid4().hex)
    paths = {"video_path": _owned_path(video_path), "events_path": _owned_path(events_path),
             "screenshots_path": _owned_path(screenshots_path)}
    with DATA_LOCK:
        ensure_dirs()
        manifest = _load_manifest()
        if recording_id in manifest:
            return recording_id
        for path in paths.values():
            if path and path.is_file():
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
        manifest[recording_id] = {
            "id": recording_id, "session_id": session_id, "tab_id": tab_id,
            "start_time": start_time, "end_time": end_time,
            "duration_ms": int((end_time - start_time) * 1000),
            "event_count": event_count, "screenshot_count": screenshot_count,
            "extension_id": extension_id, "video_size": video_size,
            **{key: str(path) if path else "" for key, path in paths.items()},
            "created_at": time.time(), "inference": None,
            "url": sanitize_url(url), "title": str(title)[:300], "goal": str(goal)[:2000],
            "capture_mode": "video" if video_path else "events",
        }
        if metadata:
            manifest[recording_id]["consent"] = metadata.get("consent") is True
            manifest[recording_id]["screenshots_consent"] = metadata.get("screenshots_consent") is True
        _enforce_ttl_and_cap(manifest)
        _save_manifest(manifest)
        return recording_id


def get_recording(recording_id: str) -> Optional[Dict[str, Any]]:
    with DATA_LOCK:
        ensure_dirs()
        manifest = _load_manifest()
        _enforce_ttl_and_cap(manifest)
        return manifest.get(_safe_id(recording_id))


def list_recordings() -> List[Dict[str, Any]]:
    with DATA_LOCK:
        ensure_dirs()
        manifest = _load_manifest()
        _enforce_ttl_and_cap(manifest)
        entries = sorted(manifest.values(), key=lambda entry: entry.get("created_at", 0), reverse=True)
        return [{
            **{key: entry.get(key) for key in (
                "id", "tab_id", "start_time", "end_time", "duration_ms", "event_count",
                "screenshot_count", "created_at", "url", "title", "goal", "capture_mode",
            )},
            "has_video": bool(entry.get("video_path")),
            "has_inference": entry.get("inference") is not None,
            "has_tickets": (ROOT / "tickets" / f"{entry['id']}.json").is_file(),
        } for entry in entries]


def _delete_artifacts(recording_id: str, entry: Dict[str, Any]) -> None:
    candidates = [entry.get(key) for key in ("video_path", "events_path", "screenshots_path")]
    candidates += [(entry.get("inference") or {}).get("result_path")]
    candidates += [ROOT / "tickets" / f"{recording_id}.json"]
    # Older versions used random filenames. Remove superseded results as well.
    for directory in (ROOT / "tickets", INFERENCE_DIR):
        if directory.is_dir():
            for path in directory.glob("*.json"):
                try:
                    payload = json.loads(path.read_text())
                    if isinstance(payload, dict) and payload.get("recording_id") == recording_id:
                        candidates.append(path)
                except (ValueError, OSError):
                    continue
    for value in candidates:
        if value:
            path = _owned_path(value)
            if path and path.is_file():
                path.unlink()


def delete_recording(recording_id: str) -> bool:
    recording_id = _safe_id(recording_id)
    with DATA_LOCK:
        ensure_dirs()
        manifest = _load_manifest()
        entry = manifest.get(recording_id)
        if entry is None:
            return False
        _delete_artifacts(recording_id, entry)
        del manifest[recording_id]
        _save_manifest(manifest)
        return True


def save_inference_result(recording_id: str, result: Dict[str, Any]) -> str:
    recording_id = _safe_id(recording_id)
    with DATA_LOCK:
        ensure_dirs()
        manifest = _load_manifest()
        if recording_id not in manifest:
            raise ValueError("Recording not found")
        result_id = uuid.uuid4().hex
        result_path = INFERENCE_DIR / f"{recording_id}.json"
        payload = {"id": result_id, "recording_id": recording_id, "created_at": time.time(), "result": result}
        write_json(result_path, payload)
        manifest[recording_id]["inference"] = {
            "result_id": result_id, "result_path": str(result_path), "created_at": payload["created_at"],
        }
        _save_manifest(manifest)
        return result_id


def get_inference_result(recording_id: str) -> Optional[Dict[str, Any]]:
    with DATA_LOCK:
        entry = get_recording(recording_id)
        if not entry or not entry.get("inference"):
            return None
        path = _owned_path(entry["inference"].get("result_path"))
        if not path or not path.is_file():
            return None
        return json.loads(path.read_text())


def _load_manifest() -> Dict[str, Any]:
    if not MANIFEST_FILE.exists():
        return {}
    # Corruption must not silently erase the library on the next save.
    payload = json.loads(MANIFEST_FILE.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Invalid capture manifest")
    return payload


def _save_manifest(manifest: Dict[str, Any]) -> None:
    write_json(MANIFEST_FILE, manifest)


def _enforce_ttl_and_cap(manifest: Dict[str, Any]) -> None:
    now = time.time()
    expired = [rid for rid, entry in manifest.items() if now - entry.get("created_at", 0) > TTL_SECONDS]
    survivors = sorted(((rid, entry) for rid, entry in manifest.items() if rid not in expired),
                       key=lambda item: item[1].get("created_at", 0))
    expired += [rid for rid, _ in survivors[:max(0, len(survivors) - MAX_RECORDINGS)]]
    for rid in expired:
        _delete_artifacts(rid, manifest[rid])
        del manifest[rid]
    if expired:
        _save_manifest(manifest)
