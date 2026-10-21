"""Secret redaction for the extension backend (defense in depth).

Mirrors the extension's frontend redaction so that secrets are never
persisted even if a client posts directly to the backend.
"""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

REDACT_FIELD_NAMES = {
    "password", "passwd", "pwd", "secret", "apikey", "api_key",
    "access_key", "secret_key", "token", "authorization", "credential",
    "private_key", "session_id", "auth", "cookie",
}
REDACT_FIELD_IDS = {
    "password", "passwd", "secret", "apikey", "api_key",
    "access_key", "secret_key", "token", "credential",
    "private_key", "session_id",
}
_SECRET_PATTERNS = [
    re.compile(r"secret|token|key|pass|cred|auth|cookie", re.IGNORECASE),
]


def sanitize_url(value: Any) -> str:
    """Keep page identity without credentials, query secrets or arbitrary hashes."""
    if not isinstance(value, str) or len(value) > 8192:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        fragment = parsed.fragment if re.fullmatch(r"/?[A-Za-z][A-Za-z0-9_/-]{0,99}", parsed.fragment) else ""
        parts = parsed.path.split("/")
        path = "/".join("REDACTED" if len(part) > 48 or (index and _looks_secret(unquote(parts[index - 1])))
                        else part for index, part in enumerate(parts))
        if _looks_secret(fragment):
            fragment = ""
        return urlunsplit((parsed.scheme, host, path, "", fragment))[:1000]
    except ValueError:
        return ""


def _looks_secret(key: str) -> bool:
    k = str(key).lower()
    if k in REDACT_FIELD_NAMES or k in REDACT_FIELD_IDS:
        return True
    return any(p.search(k) for p in _SECRET_PATTERNS)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, dict):
        sensitive = any(_looks_secret(value.get(key, "")) for key in ("name", "id", "type", "autocomplete"))
        result = {}
        for key, item in value.items():
            if _looks_secret(key) or (sensitive and key in ("value", "text", "label", "input")):
                result[key] = "***REDACTED***"
            elif str(key).lower() in ("url", "href", "page_url", "source_url"):
                result[key] = sanitize_url(item)
            else:
                result[key] = _scrub_value(item)
        return result
    return value


def sanitize_event_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    return _scrub_value(payload)


def sanitize_text(value: Any, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\bBearer\s+[a-z0-9._~+/-]+=*", "[REDACTED]", value, flags=re.I)
    text = re.sub(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][a-z0-9_-]+", "[REDACTED]", text, flags=re.I)
    text = re.sub(r"\b(?:password|token|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+", "[REDACTED]", text, flags=re.I)
    text = re.sub(r"[a-z0-9.!#$%&'*+/=?^_{}|~-]+@[a-z0-9.-]+\.[a-z]{2,}", "[EMAIL]", text, flags=re.I)
    text = re.sub(r"\b[a-z0-9_-]{40,}\b", "[REDACTED]", text, flags=re.I)
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:limit]


def sanitize_capture_event(payload: Any) -> Any:
    """Retain semantic interactions only; never typed keys, values or page text."""
    if not isinstance(payload, dict) or payload.get("type") not in ("click", "change", "submit", "navigation"):
        return None
    event = {"type": payload["type"], "url": sanitize_url(payload.get("url"))}
    timestamp = payload.get("ts")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp):
        event["ts"] = timestamp
    target = payload.get("target")
    if isinstance(target, dict):
        semantic = {}
        for key in ("tag", "role", "id", "type", "dataTab", "dataTicket"):
            if isinstance(target.get(key), str):
                semantic[key] = sanitize_text(target[key])
        tag = semantic.get("tag", "").lower()
        if tag in ("button", "a"):
            semantic["label"] = sanitize_text(target.get("label") or target.get("text"))
        if "href" in target:
            semantic["href"] = sanitize_url(target["href"])
        event["target"] = semantic
    return event


def sanitize_events(events: Any) -> Any:
    if not isinstance(events, list):
        return events
    return [clean for event in events if (clean := sanitize_capture_event(event)) is not None]
