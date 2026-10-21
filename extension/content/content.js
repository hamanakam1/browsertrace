import { sanitizeEventPayload } from "./shared/redaction.js";
import { recordConsentEvent } from "./shared/audit.js";

const INTERACTION_TYPES = new Set(["click", "keydown", "change", "submit"]);
const SCREENSHOT_TARGET_TAGS = new Set(["button", "a", "input", "select", "textarea"]);
let enabled = false;
let screenshotQueue = [];

/** Send an interaction event to the background service worker. */
function reportInteraction(type, target, wantsScreenshot = false) {
  if (!enabled) return;
  const payload = {
    type,
    ts: Date.now(),
    wantsScreenshot,
    target: {
      tag: target.tagName,
      id: target.id || null,
      name: target.name || null,
      type: target.type || null,
      href: target.href || null,
      text: (target.innerText || target.textContent || "").slice(0, 200),
    },
    coords: target.getBoundingClientRect
      ? { x: Math.round(target.getBoundingClientRect().x), y: Math.round(target.getBoundingClientRect().y) }
      : null,
  };
  const clean = sanitizeEventPayload(payload);
  chrome.runtime.sendMessage({ type: "page.interaction", payload: clean });
  if (wantsScreenshot) {
    screenshotQueue.push(Date.now());
  }
}

/** Decide whether this interaction deserves a screenshot (decision point). */
function isScreenshotWorthy(type, target) {
  if (!SCREENSHOT_TARGET_TAGS.has(target.tagName.toLowerCase())) return false;
  const text = (target.innerText || target.textContent || target.value || "").toLowerCase();
  const clicky = ["button", "a"].includes(target.tagName.toLowerCase());
  const submitty = target.type === "submit" || target.tagName.toLowerCase() === "form";
  const meaningful = /save|submit|apply|send|delete|approve|reject|create|edit|update|save|next|continue|finish|confirm|cancel|buy|purchase|pay|login|logout|sign.?in|sign.?out/.test(text);
  return (clicky || submitty) && meaningful;
}

/** Attach listeners to the current page. */
function attachListeners() {
  document.addEventListener("click", (e) => {
    const target = e.target;
    const wants = isScreenshotWorthy("click", target);
    reportInteraction("click", target, wants);
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const target = e.target;
      const wants = isScreenshotWorthy("keydown", target);
      reportInteraction("keydown", target, wants);
    }
  }, true);
  document.addEventListener("change", (e) => {
    const target = e.target;
    const wants = isScreenshotWorthy("change", target);
    reportInteraction("change", target, wants);
  }, true);
  document.addEventListener("submit", (e) => {
    const target = e.target;
    reportInteraction("submit", target, true);
  }, true);
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
  if (message.type == "extension.requestScreenshot") {
    // Content script doesn't capture tabs; background handles it.
    sendResponse({ ok: true });
    return true;
  }
  return false;
});

attachListeners();
