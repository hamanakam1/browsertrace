"""Build a replay plan for a captured flow.

The plan is a proposal for a person to review. Panther never runs it, and it
never carries a value the capture did not record. Panther does not capture
typed values, so a plan can describe the path a person took — where they went
and what they pressed — but it cannot reproduce what they typed.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from redaction import sanitize_url
from tickets import _diagnostics, _friction

SCHEMA = "https://github.com/hamanakam1/panther-intent-capture#replay-plan"
SCHEMA_VERSION = "1.0"
MAX_STEPS = 200
MAX_LABEL = 120
MAX_VALUE = 120
# An identifier that a page could regenerate, or that carries no meaning, is
# not a stable handle. Only a plain, short identifier is offered as one.
_STABLE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,48}\Z")
_REDACTED = ("REDACTED", "[EMAIL]", "[REDACTED]")


def _text(value: Any, limit: int = MAX_LABEL) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _usable(value: Any) -> bool:
    """A captured value is only usable when it contains no redaction marker."""
    if not isinstance(value, str) or not value:
        return False
    return not any(marker.casefold() in value.casefold() for marker in _REDACTED)


def _css_attribute(attribute: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{attribute}="{escaped}"]'


def _selector(target: Dict[str, Any]) -> Dict[str, Any]:
    """Choose the most stable handle available for a control.

    The order matters: a test identifier is meant to be stable, an element id
    usually is, an accessible name usually is, and a visible string is the
    weakest. When nothing is available the step is not replayable and says so.
    """
    empty = {"strategy": "none", "value": None, "attribute": None, "role": None, "css": None, "replayable": False}
    tag = _text(target.get("tag"), 40).lower()
    # The capture names these attributes in camel case; the attribute they
    # stand for on the page is the kebab-case form.
    for key, attribute in (("dataTicket", "data-ticket"), ("dataTab", "data-tab")):
        value = target.get(key)
        if _usable(value) and len(value) < MAX_VALUE:
            return {"strategy": "test-id", "value": value, "attribute": attribute, "role": None,
                    "css": _css_attribute(attribute, value), "replayable": True}
    identifier = target.get("id")
    if _usable(identifier) and _STABLE_ID.fullmatch(identifier):
        return {"strategy": "id", "value": identifier, "attribute": "id", "role": None,
                "css": f"#{identifier}", "replayable": True}
    raw_role = target.get("role")
    role = _text(raw_role, 40).lower()
    label = _text(target.get("label"), MAX_LABEL)
    # Values at the capture limit may be truncated, including halfway through
    # a redaction marker. They cannot identify the original control reliably.
    label_usable = _usable(label) and len(target["label"]) < MAX_LABEL
    role_usable = _usable(role) and len(raw_role) < 40
    # Native buttons have an implicit role even without an explicit ARIA
    # role. Their captured label may come from aria-label, not visible text.
    # Do not replace an explicit role that was redacted or truncated.
    if tag == "button" and (raw_role is None or raw_role == ""):
        role = "button"
        role_usable = True
    if role_usable and label_usable:
        return {"strategy": "role-name", "value": label, "attribute": None, "role": role,
                "css": None, "replayable": True}
    if label_usable and tag in ("button", "a"):
        return {"strategy": "text", "value": label, "attribute": None, "role": None,
                "css": None, "replayable": True}
    href = target.get("href")
    if _usable(href) and tag == "a":
        path = sanitize_url(href)
        if _usable(path) and len(path) < 1000:
            return {"strategy": "href", "value": path, "attribute": "href", "role": None,
                    "css": None, "replayable": True}
    return empty


def _step(index: int, action: str, event: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    target = event.get("target") if isinstance(event.get("target"), dict) else {}
    selector = _selector(target)
    step = {
        "index": index,
        "action": action,
        "url": sanitize_url(event.get("url")),
        "selector": selector,
        "label": _text(target.get("label"), MAX_LABEL),
        "replayable": bool(selector["replayable"]) and action in ("navigate", "click", "submit"),
    }
    if note:
        step["note"] = note[:300]
    return step


def _navigate_step(index: int, url: str) -> Dict[str, Any]:
    return {
        "index": index,
        "action": "navigate",
        "url": url,
        "selector": {"strategy": "none", "value": None, "attribute": None, "role": None, "css": None, "replayable": False},
        "label": "",
        "replayable": True,
    }


def build_plan(recording: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return a destination-neutral plan for replaying a captured flow."""
    steps: List[Dict[str, Any]] = []
    current_url = ""
    # The plan always begins by opening the captured page, so a replayer knows
    # where the flow started even when the capture recorded no navigation.
    start_url = sanitize_url(recording.get("url"))
    if start_url:
        current_url = start_url
        steps.append(_navigate_step(1, start_url))
    omitted_steps = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        step = None
        kind = event.get("type")
        if kind == "navigation":
            url = sanitize_url(event.get("url"))
            if url and url != current_url:
                current_url = url
                step = _navigate_step(len(steps) + 1, url)
        elif kind == "click":
            step = _step(len(steps) + 1, "click", event)
        elif kind == "submit":
            step = _step(len(steps) + 1, "submit", event)
            if steps and steps[-1]["action"] == "click":
                # Both native events may describe one user action. The
                # capture does not establish causality, so preserve the
                # submission for review instead of risking a second write.
                step["replayable"] = False
                step["note"] = ("The preceding click may already trigger this submission. "
                                "Review the recorded submit before adding an explicit submit action.")
        elif kind == "change":
            step = _step(len(steps) + 1, "change", event,
                         "The value of this field was never captured, so the step records that it changed, not what it became.")
        if step is not None:
            if len(steps) < MAX_STEPS:
                steps.append(step)
            else:
                omitted_steps += 1
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "producer": "panther-intent-capture",
        "generated_at": time.time(),
        "recording": {
            "id": recording.get("id"),
            "source_url": sanitize_url(recording.get("url")),
            "capture_mode": _text(recording.get("capture_mode"), 30),
            "stated_goal": _text(recording.get("goal"), 2000),
        },
        "steps": steps,
        "observations": _diagnostics(events),
        "friction": _friction(events),
        "limits": {
            "values_captured": False,
            "screenshots_analyzed": False,
            "executed_by_producer": False,
            "requires_review": True,
            "truncated": omitted_steps > 0,
            "omitted_steps": omitted_steps,
            "note": ("Panther does not capture typed values, so this plan describes the path that was taken, "
                     "not the data that was entered. It is a proposal for a person to review."),
        },
        "authority": {"export_only": True, "requires_review": True, "execution_authorized": False},
    }


def _playwright_locator(selector: Dict[str, Any]) -> Optional[str]:
    """Render one selector as a Playwright call, or nothing when it is unsafe."""
    strategy = selector.get("strategy")
    value = selector.get("value")
    css = selector.get("css")
    if strategy == "test-id" and css:
        return f'page.locator({json.dumps(css)})'
    if strategy == "id" and css:
        return f'page.locator({json.dumps(css)})'
    if strategy == "role-name" and value:
        role = selector.get("role") or "button"
        return f"page.get_by_role({json.dumps(role)}, name={json.dumps(value)}, exact=True)"
    if strategy == "text" and value:
        return f"page.get_by_text({json.dumps(value)}, exact=True)"
    if strategy == "href" and value:
        # Escape the CSS value first, then quote the complete Python argument.
        # Nesting a JSON string inside manually quoted Python lets an apostrophe
        # in a captured URL escape the generated string literal.
        return f"page.locator({json.dumps('a' + _css_attribute('href', value))})"
    return None


def render_playwright(plan: Dict[str, Any]) -> str:
    """Render a plan as a reviewable Playwright script.

    The script is a proposal. Panther does not run it, and it contains no
    captured value because no value was ever captured.
    """
    lines = [
        "# Generated by Panther Intent Capture from capture "
        f"{json.dumps(str(plan.get('recording', {}).get('id', 'unknown')))}.",
        "# This is a proposal for a person to review. Panther never runs it.",
        "# No typed value is present, because Panther never captured one.",
    ]
    omitted_steps = plan.get("limits", {}).get("omitted_steps", 0)
    if type(omitted_steps) is int and omitted_steps > 0:
        lines.append(f"# {omitted_steps} additional steps were omitted by the plan's step limit.")
    lines += [
        "",
        "from playwright.sync_api import sync_playwright",
        "",
        "",
        "def replay():",
        "    with sync_playwright() as playwright:",
        "        browser = playwright.chromium.launch()",
        "        page = browser.new_page()",
    ]
    for step in plan.get("steps", []):
        action = step.get("action")
        if action == "navigate":
            lines.append(f"        page.goto({json.dumps(step.get('url', ''))})")
            continue
        if not step.get("replayable"):
            if action == "submit":
                # Notes are untrusted captured data. Keep the source comment
                # fixed so a line break in a note cannot introduce code.
                lines.append("        # Review this recorded submission before adding an explicit submit action.")
            continue
        locator = _playwright_locator(step.get("selector") or {})
        if not locator:
            continue
        if action == "click":
            lines.append(f"        {locator}.click()")
        elif action == "submit":
            # A form is submitted, not clicked.
            lines.append(f'        {locator}.evaluate("form => form.requestSubmit()")')
    lines.append("        browser.close()")
    lines.append("")
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    replay()")
    lines.append("")
    return "\n".join(lines)
