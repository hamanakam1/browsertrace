"""Intent decomposition: turn recorded sessions into actionable tickets.

Analyzes interaction events + sampled frames from a recording and decomposes
the observed behavior into named intents (goals). Each intent becomes a
structured ticket that coding agents can act on: description, steps to
reproduce, attached screenshots, and suggested owner/component.
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.error

OLLAMA_ENDPOINT = os.environ.get("OLLAMA_ENDPOINT", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.8-hermes:latest")

TICKETS_DIR = os.path.join(os.path.expanduser("~"), ".panther-intent-capture", "tickets")
MAX_FRAMES = 12
MAX_LONG_EDGE = 768
MAX_JPEG_BYTES = 300_000


def ensure_dirs() -> None:
    os.makedirs(TICKETS_DIR, exist_ok=True)
    try:
        os.chmod(TICKETS_DIR, 0o700)
    except OSError:
        pass


def _ffprobe_duration(video_path: str) -> Optional[float]:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _extract_frame_jpeg(video_path: str, timestamp: float) -> Optional[str]:
    try:
        out = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", video_path,
                "-frames:v", "1", "-s", f"{MAX_LONG_EDGE}:{MAX_LONG_EDGE}",
                "-q:v", "4", "-f", "image2pipe", "-",
            ],
            capture_output=True, timeout=30,
        )
        if out.returncode != 0 or not out.stdout:
            return None
        data = out.stdout
        if len(data) > MAX_JPEG_BYTES:
            out2 = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", video_path,
                    "-frames:v", "1", "-s", f"{MAX_LONG_EDGE // 2}:{MAX_LONG_EDGE // 2}",
                    "-q:v", "6", "-f", "image2pipe", "-",
                ],
                capture_output=True, timeout=30,
            )
            if out2.returncode == 0 and out2.stdout:
                data = out2.stdout
        return base64.b64encode(data).decode("ascii")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _sample_key_frames(video_path: str, event_timestamps: List[float]) -> List[Dict[str, Any]]:
    """Sample frames at key interaction timestamps, plus uniform coverage."""
    duration = _ffprobe_duration(video_path) or 120.0
    key_times = sorted(set([min(max(t, 0), duration) for t in event_timestamps[:8]]))
    # Add uniform coverage frames
    uniform = [duration * (i + 1) / 5 for i in range(4)]
    all_times = sorted(set(key_times + uniform))
    frames: List[Dict[str, Any]] = []
    for ts in all_times[:MAX_FRAMES]:
        jpeg = _extract_frame_jpeg(video_path, ts)
        if jpeg:
            frames.append({"timestamp": round(ts, 1), "jpeg": jpeg})
    return frames


def _ollama_decompose(frames: List[Dict[str, Any]], events_summary: str) -> str:
    """Ask Ollama to decompose the session into named intents with ticket structure."""
    image_blocks = []
    for f in frames:
        image_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{f['jpeg']}"},
        })
    messages = [
        {
            "role": "system",
            "content": (
                "You decompose a user's page usage recording into named intents (goals). "
                "Each intent becomes an actionable ticket for a coding agent. "
                "Return a JSON array of ticket objects with these fields: "
                "title (short), description (what the user wanted), "
                "steps_to_reproduce (array of short steps), screenshots (array of frame timestamps referenced), "
                "component (suggested code area), priority (low/medium/high), "
                "evidence (what in the recording supports this intent). "
                "Focus on distinct user goals: form submissions, navigations, data entries, approvals. "
                "Be concise and actionable. Return ONLY the JSON array, no markdown fences."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Interaction events:\n{events_summary}\n\nDecompose this session into intent tickets:"},
                *image_blocks,
            ],
        },
    ]
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1200,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{OLLAMA_ENDPOINT}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("choices", [{}])[0].get("message", {}).get("content", "")


def summarize_events_for_decomposition(events: List[Dict[str, Any]]) -> str:
    if not events:
        return "No interaction events recorded."
    lines = []
    for e in events[:300]:
        ts = e.get("ts", 0)
        t = e.get("type", "?")
        target = e.get("target", {})
        tag = target.get("tag", "?")
        text = (target.get("text") or "")[:60]
        coords = target.get("coords")
        coord_str = f"({coords.get('x')},{coords.get('y')})" if coords else ""
        lines.append(f"  {ts}ms | {t} | <{tag}> {coord_str} | {text}")
    return "\n".join(lines)


def decompose_session(video_path: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Decompose a recording into intent tickets. Returns structured ticket data."""
    start = time.time()
    event_timestamps = [e.get("ts", 0) for e in events if isinstance(e, dict)]
    frames = _sample_key_frames(video_path, event_timestamps)
    events_summary = summarize_events_for_decomposition(events)
    try:
        raw = _ollama_decompose(frames, events_summary)
    except Exception as exc:  # noqa: BLE001
        raw = f"[]"
    # Parse JSON from Ollama response; fall back gracefully.
    tickets: List[Dict[str, Any]] = []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            tickets = parsed
    except (json.JSONDecodeError, ValueError):
        # Try to extract a JSON array from the text.
        import re as _re
        match = _re.search(r"\[.*\]", raw, _re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    tickets = parsed
            except (json.JSONDecodeError, ValueError):
                tickets = []
    # Normalize tickets.
    normalized = []
    for i, t in enumerate(tickets):
        if not isinstance(t, dict):
            continue
        normalized.append({
            "id": f"ticket-{i + 1}",
            "title": str(t.get("title", "Untitled intent"))[:120],
            "description": str(t.get("description", ""))[:1000],
            "steps_to_reproduce": t.get("steps_to_reproduce", [])[:10],
            "screenshots": [f for f in (t.get("screenshots", []) or []) if isinstance(f, (int, float))][:6],
            "component": str(t.get("component", "unspecified"))[:100],
            "priority": str(t.get("priority", "medium"))[:20],
            "evidence": str(t.get("evidence", ""))[:500],
        })
    return {
        "tickets": normalized,
        "frames_sampled": len(frames),
        "events_count": len(events),
        "duration_seconds": round(time.time() - start, 2),
        "model": OLLAMA_MODEL,
    }


def save_tickets(recording_id: str, data: Dict[str, Any]) -> str:
    ensure_dirs()
    ticket_id = uuid.uuid4().hex
    payload = {
        "id": ticket_id,
        "recording_id": recording_id,
        "created_at": time.time(),
        "data": data,
    }
    path = os.path.join(TICKETS_DIR, f"{ticket_id}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return ticket_id


def load_tickets(recording_id: str) -> Optional[Dict[str, Any]]:
    ensure_dirs()
    if not os.path.isdir(TICKETS_DIR):
        return None
    for fname in os.listdir(TICKETS_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(TICKETS_DIR, fname)) as f:
                payload = json.load(f)
            if payload.get("recording_id") == recording_id:
                return payload
        except (json.JSONDecodeError, OSError):
            continue
    return None
