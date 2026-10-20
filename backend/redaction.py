"""Secret redaction for the extension backend (defense in depth).

Mirrors the extension's frontend redaction so that secrets are never
persisted even if a client posts directly to the backend.
"""

from __future__ import annotations

import re
from typing import Any

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
        return {k: ("***REDACTED***" if _looks_secret(k) else _scrub_value(v)) for k, v in value.items()}
    return value


def sanitize_event_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    out: dict = {}
    for k, v in payload.items():
        if _looks_secret(k):
            out[k] = "***REDACTED***"
        else:
            out[k] = _scrub_value(v)
    return out


def sanitize_events(events: Any) -> Any:
    if not isinstance(events, list):
        return events
    return [sanitize_event_payload(e) if isinstance(e, dict) else e for e in events]
