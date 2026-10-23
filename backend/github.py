"""An explicit GitHub Issues destination.

This is Panther's first remote destination, and it is deliberately narrow. The
host is fixed, the repository is named explicitly, the credential is read only
here, and the destination is never reachable unless it has been configured.

It is not a general webhook client. The dashboard adapter stays loopback-only
and its host checks are not relaxed to make this one work.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from . import storage
except ImportError:  # python backend/server.py
    import storage

import integrations

API_HOST = "https://api.github.com"
TOKEN_ENV = "PANTHER_GITHUB_TOKEN"
REPO_ENV = "PANTHER_GITHUB_REPO"
KIND = "github"
FENCE = integrations.FENCE
REQUEST_TIMEOUT = 15
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ISSUES_PER_PAGE = 100
MAX_ISSUES_SCANNED = 500
SCAN_TIMEOUT = 15
_LABEL = "panther"
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38})/[A-Za-z0-9](?:[A-Za-z0-9._-]{0,38})\Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ValueError("GitHub redirects are not followed; configure the destination directly")


def _read_file(name: str) -> str:
    path = storage.ROOT / "github" / name
    if not path.is_file():
        return ""
    try:
        if path.stat().st_mode & 0o077:
            # A credential readable by anyone else is not used.
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _token() -> str:
    value = (os.environ.get(TOKEN_ENV) or "").strip()
    return value or _read_file("token")


def _repo() -> str:
    value = (os.environ.get(REPO_ENV) or "").strip()
    return value or _read_file("repo")


def _valid_repo(value: str) -> bool:
    return bool(value) and bool(_REPO.fullmatch(value))


def _valid_token(value: str) -> bool:
    return len(value) >= 20 and not any(character.isspace() for character in value)


def configuration() -> Dict[str, Any]:
    """What is configured, without ever revealing the credential."""
    repo = _repo()
    token = _token()
    return {
        "configured": _valid_repo(repo) and _valid_token(token),
        "repo": repo if _valid_repo(repo) else "",
        "host": API_HOST,
        "credential": "present" if _valid_token(token) else "missing",
    }


def target() -> str:
    """The canonical destination this configuration names."""
    repo = _repo()
    if not _valid_repo(repo):
        raise ValueError("Configure a GitHub repository as owner/name before using this destination")
    return f"{API_HOST}/repos/{repo}"


def _request(path: str, payload: Optional[dict] = None, *, before_send=None,
             timeout: float = REQUEST_TIMEOUT) -> dict:
    """One authenticated GitHub request. The token never appears in a result."""
    token = _token()
    if not _valid_token(token):
        raise ValueError("Configure a GitHub credential before using this destination")
    request = urllib.request.Request(
        API_HOST + path,
        data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") if payload is not None else None,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                 "Authorization": f"Bearer {token}",
                 **({"Content-Type": "application/json"} if payload is not None else {})},
        method="POST" if payload is not None else "GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    if payload is not None:
        if integrations.BEFORE_POST is not None:
            integrations.BEFORE_POST()
        if before_send is not None:
            before_send()
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        # Remote error bodies may echo credentials or private request content.
        raise ValueError(f"GitHub returned HTTP {status}") from None
    with response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("GitHub response exceeded the bounded read limit")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("GitHub returned a response that could not be read") from None
    if not isinstance(parsed, (dict, list)):
        raise ValueError("GitHub returned an unexpected response")
    return parsed


def status() -> Dict[str, Any]:
    """Reachability and credential check. No issue data is read."""
    config = configuration()
    if not config["configured"]:
        return {"ok": False, "state": "unconfigured", "url": "", "repo": config["repo"],
                "error": "Configure a GitHub repository and credential before using this destination."}
    try:
        repository = _request(f"/repos/{config['repo']}")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"ok": False, "state": "unavailable", "url": target(), "repo": config["repo"],
                "error": _error(exc)}
    if not isinstance(repository, dict) or repository.get("full_name") != config["repo"]:
        return {"ok": False, "state": "unavailable", "url": target(), "repo": config["repo"],
                "error": "GitHub did not confirm the configured repository."}
    return {"ok": True, "state": "reachable", "url": target(), "repo": config["repo"],
            "detail": "The repository is reachable. No issue has been read or created."}


def _result(receipt: dict, previously_verified: bool = False) -> dict:
    """Report a delivery outcome without ever exposing the credential."""
    result = {"ok": receipt.get("state") == "delivered", "state": receipt.get("state", "pending"),
              "verified": receipt.get("verified") is True, "previously_verified": previously_verified,
              "verified_at": receipt.get("verified_at"), "receipt": receipt}
    if receipt.get("item"):
        number = receipt["item"]
        repo = urllib.parse.urlsplit(receipt["target"]).path.removeprefix("/repos/")
        result.update(ticket_key=f"#{number}", ticket_url=f"https://github.com/{repo}/issues/{number}",
                      created=receipt.get("created"))
    if receipt.get("error"):
        result["error"] = receipt["error"]
    return result


def _error(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return f"{str(exc)[:200]}. No delivery is confirmed."
    return "GitHub could not be reached or timed out; any retry uses the same capture identity."


def _search_marker(recording_id: str, ticket_id: str) -> str:
    """A stable, searchable identity for one draft of one capture."""
    return f"panther-capture-{recording_id}-{ticket_id}"


def _detail(record: dict, draft: dict, seed: str) -> str:
    """The bounded body of the issue, with the review fence and the identity."""
    header = (
        f"{FENCE}.\nReview-only Panther agent proposal.\n"
        f"Capture: {record.get('id', 'unknown')} | Draft: {draft.get('id', 'unknown')}\n"
        f"Source: {str(record.get('source_url') or 'not captured')[:400]}\n"
        f"Recorded: {record.get('start_time', 'unknown')} to {record.get('end_time', 'unknown')} (capture timestamps)\n"
    )
    sections = [header,
                "Intent / proposed change:\n" + str(draft.get("description", ""))[:750],
                "Observed evidence:\n" + str(draft.get("evidence", ""))[:450],
                "Steps to reproduce:\n" + "\n".join(draft.get("steps_to_reproduce", []))[:450],
                "Expected result / acceptance criteria:\n" + "\n".join(draft.get("acceptance_criteria", []))[:450],
                "Evidence references:\n" + json.dumps({"refs": draft.get("evidence_refs", []),
                                                        "frame_seconds": draft.get("screenshot_timestamps", [])},
                                                       ensure_ascii=False)[:300],
                f"Identity: {seed}",
                ]
    return "\n\n".join(sections)[:3900]


def preview_ticket(recording: dict, ticket: dict) -> Dict[str, Any]:
    """Build the exact issue payload. This function never contacts GitHub."""
    destination = target()
    record, draft = integrations._recording(recording), integrations._draft(ticket)
    seed = f"panther:capture:{record['id']}:{draft['id']}"
    identity = _search_marker(record["id"], draft["id"])
    body = _detail(record, draft, identity)
    title = str(draft.get("title") or "")[:256]
    if not title:
        raise ValueError("Ticket title is required")
    payload = {
        "title": title,
        "body": body,
        "labels": [_LABEL, "needs-triage", "no-auto"],
    }
    fingerprint = hashlib.sha256(json.dumps(
        {"target_url": destination, "payload": payload, "recording": record, "draft": draft},
        sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with storage.DATA_LOCK:
        receipt = integrations._load_receipt(destination, seed)
    warnings = ["Created issues are review-fenced: the body states that execution is not authorized.",
                "The no-auto label is a convenience; the review fence is the text in the body.",
                "A bounded summary is sent; the full neutral export remains available in Panther."]
    return {"ok": True, "target": KIND, "target_url": destination, "payload": payload,
            "seed_key": seed, "payload_hash": fingerprint, "warnings": warnings, "receipt": receipt}


def _find_existing(seed: str, repo: str) -> Optional[dict]:
    """Find a marker within a bounded, paginated scan of the pinned repository."""
    deadline = time.monotonic() + SCAN_TIMEOUT
    for page in range(1, MAX_ISSUES_SCANNED // ISSUES_PER_PAGE + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("The issue scan time budget expired; no creation was attempted")
        # Construct every page locally. Never follow an arbitrary pagination URL
        # returned by a remote response with the configured credential.
        issues = _request(
            f"/repos/{repo}/issues?state=all&per_page={ISSUES_PER_PAGE}&sort=created&direction=desc&page={page}",
            timeout=min(REQUEST_TIMEOUT, remaining),
        )
        if time.monotonic() >= deadline:
            raise ValueError("The issue scan time budget expired; no creation was attempted")
        if not isinstance(issues, list) or len(issues) > ISSUES_PER_PAGE:
            raise ValueError("GitHub returned an unexpected issue list")
        for issue in issues:
            if (not isinstance(issue, dict) or type(issue.get("number")) is not int
                    or issue["number"] <= 0
                    or (issue.get("body") is not None and not isinstance(issue["body"], str))):
                raise ValueError("GitHub returned an unexpected issue entry")
            if "pull_request" in issue:
                continue
            if isinstance(issue.get("body"), str) and f"Identity: {seed}" in issue["body"].splitlines():
                return issue
        if len(issues) < ISSUES_PER_PAGE:
            return None
    raise ValueError("The issue scan reached its entry limit; review the destination before creating an issue")


def _verify(seed: str, issue: dict, payload: dict, expected_item: int) -> None:
    """Confirm the returned issue is exactly the one that was asked for."""
    if (type(issue.get("number")) is not int or issue["number"] <= 0
            or issue["number"] != expected_item or "pull_request" in issue
            or issue.get("title") != payload["title"]
            or issue.get("body") != payload["body"]
            or f"Identity: {seed}" not in payload["body"].splitlines()
            or issue.get("state") not in ("open", "closed")):
        raise ValueError("GitHub did not confirm the exact issue identity; delivery remains pending.")


def submit_ticket(recording: dict, ticket: dict, *, before_send=None, expected_payload_hash=None) -> Dict[str, Any]:
    """Create one confirmed issue and verify it, reusing a durable receipt."""
    # Automation already holds this reentrant lock when submitting. Reuse its
    # lock order rather than adding an adapter lock that can deadlock with it.
    with storage.DATA_LOCK:
        return _submit_ticket(recording, ticket, before_send=before_send,
                              expected_payload_hash=expected_payload_hash)


def _submit_ticket(recording: dict, ticket: dict, *, before_send=None, expected_payload_hash=None) -> Dict[str, Any]:
    preview = preview_ticket(recording, ticket)
    if expected_payload_hash is not None and preview["payload_hash"] != expected_payload_hash:
        return {"ok": False, "state": "conflict", "verified": False,
                "error": "The confirmed destination or payload changed; preview it again before sending"}
    destination, payload, seed = preview["target_url"], preview["payload"], preview["seed_key"]
    repo = urllib.parse.urlsplit(destination).path.removeprefix("/repos/")
    identity = _search_marker(recording["id"], ticket["id"])
    with storage.DATA_LOCK:
        prior = integrations._load_receipt(destination, seed)
        if prior and prior.get("payload_hash") != preview["payload_hash"]:
            return {"ok": False, "state": "conflict", "verified": False, "receipt": prior,
                    "error": ("This draft was already submitted to GitHub with different content. "
                              "Existing issues are never silently updated; review that issue or create a distinct draft.")}
        if prior and prior.get("state") == "delivered":
            return _result(prior, previously_verified=True)
        receipt = dict(prior or {}, target=destination, seed_key=seed, payload_hash=preview["payload_hash"],
                       state="pending", verified=False, updated_at=time.time())
        # Older pending receipts without a marker cannot establish whether a
        # POST was sent. Treat that uncertainty as an attempted creation.
        receipt.setdefault("creation_attempted", prior is not None)
        receipt.pop("error", None)
        path = integrations._receipt_path(destination, seed)
        storage.write_json(path, receipt)  # a write failure must prevent the request
    try:
        if not receipt.get("item"):
            existing = _find_existing(identity, repo)
            if existing:
                number = existing.get("number")
                if type(number) is not int or number <= 0:
                    raise ValueError("GitHub did not return a valid issue number")
                receipt.update(item=number, created=False, updated_at=time.time())
            else:
                if receipt["creation_attempted"]:
                    raise ValueError("A previous creation is uncertain and its issue was not found; review the destination before retrying")

                def guard_creation():
                    if before_send is not None:
                        before_send()
                    if target() != destination:
                        raise ValueError("The configured GitHub destination changed; preview it again before sending")
                    receipt.update(creation_attempted=True, updated_at=time.time())
                    storage.write_json(path, receipt)

                response = _request(f"/repos/{repo}/issues", payload, before_send=guard_creation)
                if not isinstance(response, dict) or type(response.get("number")) is not int or response["number"] <= 0:
                    raise ValueError("GitHub did not return a valid issue number")
                receipt.update(item=response["number"], created=True, updated_at=time.time())
            storage.write_json(path, receipt)
        readback = _request(f"/repos/{repo}/issues/{receipt['item']}")
        if not isinstance(readback, dict):
            raise ValueError("GitHub did not return the created issue")
        _verify(identity, readback, payload, receipt["item"])
        receipt.update(state="delivered", verified=True, verified_at=time.time(), updated_at=time.time())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        receipt.update(state="pending", verified=False, error=_error(exc), updated_at=time.time())
    storage.write_json(path, receipt)
    return _result(receipt)
