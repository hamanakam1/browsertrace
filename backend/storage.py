"""File-backed storage for extension recordings under ~/.dashboard-extension/."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
ROOT = HOME / ".dashboard-extension"
RECORDINGS_DIR = ROOT / "recordings"
EVENTS_DIR = ROOT / "events"
INFERENCE_DIR = ROOT / "inference"
CONSENT_LOG = ROOT / "consent.log"
MANIFEST_FILE = ROOT / "manifest.json"

MAX_RECORDINGS = 200
TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


def ensure_dirs() -> None:
    for d in (ROOT, RECORDINGS_DIR, EVENTS_DIR, INFERENCE_DIR):
        d.mkdir(parents=True, exist_ok=True)
        # owner-only permissions for sensitive local data
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _safe_id(recording_id: str) -> str:
    if not recording_id or ".." in recording_id or "/" in recording_id:
        raise ValueError("Invalid recording id")
    return recording_id


def create_recording(
    *,
    tab_id: int,
    start_time: float,
    end_time: float,
    event_count: int,
    extension_id: str,
    video_path: Path,
    events_path: Path,
    video_size: int,
) -> str:
    ensure_dirs()
    recording_id = uuid.uuid4().hex
    manifest = _load_manifest()
    entry = {
        "id": recording_id,
        "tab_id": tab_id,
        "start_time": start_time,
        "end_time": end_time,
        "duration_ms": int((end_time - start_time) * 1000),
        "event_count": event_count,
        "extension_id": extension_id,
        "video_path": str(video_path),
        "events_path": str(events_path),
        "video_size": video_size,
        "created_at": time.time(),
        "inference": None,
    }
    manifest[recording_id] = entry
    _save_manifest(manifest)
    _enforce_ttl_and_cap(manifest)
    return recording_id


def get_recording(recording_id: str) -> Optional[Dict[str, Any]]:
    ensure_dirs()
    manifest = _load_manifest()
    return manifest.get(_safe_id(recording_id))


def list_recordings() -> List[Dict[str, Any]]:
    ensure_dirs()
    manifest = _load_manifest()
    entries = sorted(manifest.values(), key=lambda e: e.get("created_at", 0), reverse=True)
    # Return summary fields only (no full paths in public list)
    return [
        {
            "id": e["id"],
            "tab_id": e["tab_id"],
            "start_time": e["start_time"],
            "end_time": e["end_time"],
            "duration_ms": e["duration_ms"],
            "event_count": e["event_count"],
            "created_at": e["created_at"],
            "has_inference": e.get("inference") is not None,
        }
        for e in entries
    ]


def delete_recording(recording_id: str) -> bool:
    ensure_dirs()
    recording_id = _safe_id(recording_id)
    manifest = _load_manifest()
    entry = manifest.pop(recording_id, None)
    if entry is None:
        return False
    for key in ("video_path", "events_path"):
        p = Path(entry.get(key, ""))
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    # remove inference result if present
    inf = entry.get("inference")
    if inf:
        p = Path(inf.get("result_path", ""))
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    _save_manifest(manifest)
    return True


def save_inference_result(recording_id: str, result: Dict[str, Any]) -> str:
    ensure_dirs()
    recording_id = _safe_id(recording_id)
    result_id = uuid.uuid4().hex
    result_path = INFERENCE_DIR / f"{result_id}.json"
    payload = {
        "id": result_id,
        "recording_id": recording_id,
        "created_at": time.time(),
        "result": result,
    }
    result_path.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(result_path, 0o600)
    except OSError:
        pass
    manifest = _load_manifest()
    if recording_id in manifest:
        manifest[recording_id]["inference"] = {
            "result_id": result_id,
            "result_path": str(result_path),
            "created_at": payload["created_at"],
        }
        _save_manifest(manifest)
    return result_id


def get_inference_result(recording_id: str) -> Optional[Dict[str, Any]]:
    ensure_dirs()
    manifest = _load_manifest()
    entry = manifest.get(_safe_id(recording_id))
    if not entry or not entry.get("inference"):
        return None
    inf = entry["inference"]
    p = Path(inf.get("result_path", ""))
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _load_manifest() -> Dict[str, Any]:
    if not MANIFEST_FILE.exists():
        return {}
    try:
        return json.loads(MANIFEST_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(manifest: Dict[str, Any]) -> None:
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2))
    try:
        os.chmod(MANIFEST_FILE, 0o600)
    except OSError:
        pass


def _enforce_ttl_and_cap(manifest: Dict[str, Any]) -> None:
    now = time.time()
    expired = [
        rid for rid, e in manifest.items()
        if now - e.get("created_at", 0) > TTL_SECONDS
    ]
    for rid in expired:
        entry = manifest.pop(rid, None)
        if entry:
            for key in ("video_path", "events_path"):
                p = Path(entry.get(key, ""))
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
    # cap total count
    if len(manifest) > MAX_RECORDINGS:
        ordered = sorted(manifest.items(), key=lambda kv: kv[1].get("created_at", 0))
        for rid, entry in ordered[: len(manifest) - MAX_RECORDINGS]:
            manifest.pop(rid, None)
            for key in ("video_path", "events_path"):
                p = Path(entry.get(key, ""))
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
    _save_manifest(manifest)
