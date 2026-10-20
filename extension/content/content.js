import { sanitizeEventPayload } from "./shared/redaction.js";
import { recordConsentEvent } from "./shared/audit.js";
import { DASHBOARD_ORIGIN } from "./shared/constants.js";

const INTERACTION_TYPES = new Set(["click", "keydown", "change", "submit"]);
let enabled = false;

/** Send an interaction event to the background service worker. */
function reportInteraction(type, target) {
  if (!enabled) return;
  const payload = {
    type,
    ts: Date.now(),
    tabId: (chrome.runtime && chrome.runtime.id) ? null : null,
    target: {
      tag: target.tagName,
      id: target.id || null,
      name: target.name || null,
      type: target.type || null,
      href: target.href || null,
      text: (target.innerText || target.textContent || "").slice(0, 200)
    },
    coords: target.getBoundingClientRect
      ? { x: Math.round(target.getBoundingClientRect().x), y: Math.round(target.getBoundingClientRect().y) }
      : null
  };
  const clean = sanitizeEventPayload(payload);
  chrome.runtime.sendMessage({ type: "dashboard.interaction", payload: clean });
}

/** Attach listeners to the dashboard document. */
function attachListeners() {
  document.addEventListener("click", (e) => reportInteraction("click", e.target), true);
  document.addEventListener("keydown", (e) => reportInteraction("keydown", e.target), true);
  document.addEventListener("change", (e) => reportInteraction("change", e.target), true);
  document.addEventListener("submit", (e) => reportInteraction("submit", e.target), true);
}

/** Listen for enable/disable commands from the background worker. */
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "extension.setEnabled") {
    enabled = !!message.enabled;
    recordConsentEvent(enabled ? "interactions_enabled" : "interactions_disabled");
    sendResponse({ ok: true, enabled });
    return true;
  }
  if (message.type === "extension.isEnabled") {
    sendResponse({ ok: true, enabled });
    return true;
  }
});

attachListeners();
