"""HTTP API route handlers for the extension backend."""

from __future__ import annotations

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional, Tuple

from storage import (
    create_recording,
    delete_recording,
    get_inference_result,
    get_recording,
    list_recordings,
    save_inference_result,
)
from inference import run_inference
from redaction import sanitize_events

UPLOAD_LIMIT = 64 * 1024 * 1024  # 64MB max upload


def _parse_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", 0))
    if length > UPLOAD_LIMIT:
        raise ValueError("Request body too large")
    return handler.rfile.read(length)


def _json(handler: BaseHTTPRequestHandler, code: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _route(method: str, path: str) -> Optional[Tuple[str, Dict[str, str]]]:
    """Match a route. Returns (handler_name, path_params) or None."""
    parts = [p for p in path.split("?")[0].split("/") if p]
    if method == "POST" and parts == ["api", "extension", "record"]:
        return ("record", {})
    if method == "GET" and parts == ["api", "extension", "recordings"]:
        return ("list_recordings", {})
    if method == "GET" and len(parts) == 4 and parts[:3] == ["api", "extension", "recordings"]:
        return ("get_recording", {"id": parts[3]})
    if method == "DELETE" and len(parts) == 4 and parts[:3] == ["api", "extension", "recordings"]:
        return ("delete_recording", {"id": parts[3]})
    if method == "POST" and len(parts) == 4 and parts[:3] == ["api", "extension", "infer"]:
        return ("infer", {"id": parts[3]})
    if method == "GET" and len(parts) == 4 and parts[:3] == ["api", "extension", "infer"]:
        return ("get_inference", {"id": parts[3]})
    if method == "GET" and parts == ["api", "extension", "health"]:
        return ("health", {})
    return None


def handle_request(handler: BaseHTTPRequestHandler) -> None:
    method = handler.command
    path = handler.path
    matched = _route(method, path)
    if not matched:
        _json(handler, 404, {"error": "not found"})
        return

    name, params = matched

    try:
        if name == "health":
            _json(handler, 200, {"status": "ok", "service": "dashboard-extension-backend"})
            return

        if name == "record":
            # multipart form: video (file), events (json string), tabId, startTime, endTime, extensionId
            content_type = handler.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                _json(handler, 400, {"error": "expected multipart/form-data"})
                return
            fields, files = _parse_multipart(handler)
            video_file = files.get("video")
            if not video_file:
                _json(handler, 400, {"error": "missing video"})
                return
            events_raw = fields.get("events", "[]")
            try:
                events = json.loads(events_raw)
            except json.JSONDecodeError:
                events = []
            events = sanitize_events(events)
            tab_id = int(fields.get("tabId", "0") or 0)
            start_time = float(fields.get("startTime", "0") or 0)
            end_time = float(fields.get("endTime", str(time.time())) or time.time())
            extension_id = fields.get("extensionId", "unknown")

            # write video + events to disk
            rec_dir = os.path.join(os.path.expanduser("~"), ".dashboard-extension", "recordings")
            os.makedirs(rec_dir, exist_ok=True)
            rec_id = uuid.uuid4().hex
            video_name = f"{rec_id}.webm"
            events_name = f"{rec_id}.json"
            video_path = os.path.join(rec_dir, video_name)
            events_path = os.path.join(rec_dir, events_name)
            with open(video_path, "wb") as f:
                f.write(video_file)
            with open(events_path, "w") as f:
                json.dump(events, f)

            recording_id = create_recording(
                tab_id=tab_id,
                start_time=start_time,
                end_time=end_time,
                event_count=len(events) if isinstance(events, list) else 0,
                extension_id=extension_id,
                video_path=video_path,
                events_path=events_path,
                video_size=os.path.getsize(video_path),
            )
            _json(handler, 201, {"id": recording_id, "event_count": len(events) if isinstance(events, list) else 0})
            return

        if name == "list_recordings":
            _json(handler, 200, {"recordings": list_recordings()})
            return

        if name == "get_recording":
            entry = get_recording(params["id"])
            if not entry:
                _json(handler, 404, {"error": "recording not found"})
                return
            _json(handler, 200, entry)
            return

        if name == "delete_recording":
            ok = delete_recording(params["id"])
            if not ok:
                _json(handler, 404, {"error": "recording not found"})
                return
            _json(handler, 200, {"deleted": True})
            return

        if name == "infer":
            entry = get_recording(params["id"])
            if not entry:
                _json(handler, 404, {"error": "recording not found"})
                return
            # load events
            events_path = entry.get("events_path", "")
            events = []
            if events_path and os.path.exists(events_path):
                try:
                    events = json.loads(open(events_path).read())
                except (json.JSONDecodeError, OSError):
                    events = []
            result = run_inference(entry.get("video_path", ""), events)
            result_id = save_inference_result(params["id"], result)
            _json(handler, 200, {"id": result_id, "result": result})
            return

        if name == "get_inference":
            result = get_inference_result(params["id"])
            if not result:
                _json(handler, 404, {"error": "inference result not found"})
                return
            _json(handler, 200, result)
            return

    except ValueError as exc:
        _json(handler, 400, {"error": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001
        _json(handler, 500, {"error": "internal error", "detail": str(exc)})
        return


def _parse_multipart(handler: BaseHTTPRequestHandler) -> Tuple[Dict[str, str], Dict[str, bytes]]:
    """Minimal multipart/form-data parser."""
    content_type = handler.headers.get("Content-Type", "")
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
            break
    if not boundary:
        raise ValueError("missing boundary")

    body = _parse_body(handler)
    delim = ("--" + boundary).encode()
    fields: Dict[str, str] = {}
    files: Dict[str, bytes] = {}

    segments = body.split(delim)
    for seg in segments:
        seg = seg.strip()
        if not seg or seg == b"--":
            continue
        if seg.endswith(b"--"):
            seg = seg[:-2].strip()
        header_end = seg.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        header_block = seg[:header_end].decode("utf-8", errors="replace")
        data = seg[header_end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]

        name = None
        filename = None
        for line in header_block.split("\r\n"):
            line = line.strip()
            if line.lower().startswith("content-disposition:"):
                for token in line.split(";"):
                    token = token.strip()
                    if token.startswith("name="):
                        name = token[len("name="):].strip('"')
                    elif token.startswith("filename="):
                        filename = token[len("filename="):].strip('"')

        if filename is None:
            fields[name] = data.decode("utf-8", errors="replace")
        else:
            files[name] = data

    return fields, files
