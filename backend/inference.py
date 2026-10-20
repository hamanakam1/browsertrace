"""Inference pipeline: sample frames from a recording, run local inference."""

from __future__ import annotations

import base64
import io
import json
import math
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.error

OLLAMA_ENDPOINT = os.environ.get("OLLAMA_ENDPOINT", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.8-hermes:latest")
MAX_FRAMES = 8
MAX_LONG_EDGE = 1024
MAX_JPEG_BYTES = 350_000


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


def _sample_frames(video_path: str, max_frames: int = MAX_FRAMES) -> List[str]:
    """Uniformly sample frames from the video, return base64 JPEGs."""
    duration = _ffprobe_duration(video_path)
    if duration is None or duration <= 0:
        duration = 120.0  # fallback
    timestamps = [duration * (i + 1) / (max_frames + 1) for i in range(max_frames)]
    frames: List[str] = []
    for ts in timestamps:
        jpeg = _extract_frame_jpeg(video_path, ts)
        if jpeg:
            frames.append(jpeg)
    return frames


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
            # downscale further via a second pass at lower quality
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


def _ollama_chat(frames: List[str], events_summary: str) -> str:
    """Send sampled frames + event summary to Ollama, return text."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI assistant analyzing a page usage recording. "
                "You receive sampled video frames (base64 JPEG) and a list of click/interaction "
                "events with timestamps. Describe what the user did, which page areas they "
                "touched, any patterns or friction, and suggest improvements. Be concise."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Interaction events:\n{events_summary}\n\nHere are sampled frames from the recording:"},
                *[
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{f}"}}
                    for f in frames
                ],
            ],
        },
    ]
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 600,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{OLLAMA_ENDPOINT}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("choices", [{}])[0].get("message", {}).get("content", "")


def summarize_events(events: List[Dict[str, Any]]) -> str:
    if not events:
        return "No interaction events recorded."
    lines = []
    for e in events[:200]:
        ts = e.get("ts", 0)
        t = e.get("type", "?")
        target = e.get("target", {})
        tag = target.get("tag", "?")
        text = (target.get("text") or "")[:60]
        lines.append(f"  {ts}ms | {t} | <{tag}> | {text}")
    return "\n".join(lines)


def run_inference(video_path: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Run inference on a recording: sample frames, ask Ollama, return result."""
    start = time.time()
    frames = _sample_frames(video_path)
    events_summary = summarize_events(events)
    try:
        narrative = _ollama_chat(frames, events_summary)
    except Exception as exc:  # noqa: BLE001 - surface inference errors gracefully
        narrative = f"Inference failed: {exc}"
    return {
        "model": OLLAMA_MODEL,
        "frames_sampled": len(frames),
        "events_count": len(events),
        "duration_seconds": round(time.time() - start, 2),
        "narrative": narrative,
    }
