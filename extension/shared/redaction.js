import { REDACT_FIELD_NAMES, REDACT_FIELD_IDS } from "./constants.js";

function looksSecret(key) {
  const k = String(key).toLowerCase();
  return REDACT_FIELD_NAMES.has(k) || REDACT_FIELD_IDS.has(k) || /secret|token|key|pass|cred|auth|cookie/.test(k);
}

function scrubValue(value) {
  if (typeof value === "string") return value;
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value;
  if (Array.isArray(value)) return value.map(scrubValue);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = looksSecret(k) ? "***REDACTED***" : scrubValue(v);
    }
    return out;
  }
  return value;
}

/** Recursively redact secret-looking fields from an event payload. */
export function sanitizeEventPayload(payload) {
  if (!payload || typeof payload !== "object") return payload;
  const out = {};
  for (const [k, v] of Object.entries(payload)) {
    if (looksSecret(k)) {
      out[k] = "***REDACTED***";
    } else {
      out[k] = scrubValue(v);
    }
  }
  return out;
}

/** Remove URL credentials, all query values, and secret-bearing fragments. */
export function sanitizeUrl(value) {
  try {
    const url = new URL(String(value));
    if (!/^https?:$/.test(url.protocol)) return "";
    url.username = "";
    url.password = "";
    url.search = "";
    if (!/^#\/?[a-z][a-z0-9_/-]{0,100}$/i.test(url.hash) || /secret|token|key|pass|cred|auth|cookie|session/i.test(url.hash)) url.hash = "";
    url.pathname = url.pathname.split("/").map((part, i, parts) =>
      /secret|token|key|pass|cred|auth|cookie|session/i.test(decodeURIComponent(parts[i - 1] || "")) || part.length > 48 ? "REDACTED" : part).join("/");
    return url.href.slice(0, 1000);
  } catch { return ""; }
}

export function sanitizeText(value, max = 120) {
  return String(value ?? "")
    .replace(/\b(?:Bearer\s+)[a-z0-9._~+/-]+=*/gi, "[REDACTED]")
    .replace(/\b(?:sk|ghp|github_pat|xox[baprs])[-_][a-z0-9_-]+/gi, "[REDACTED]")
    .replace(/\b(?:password|token|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+/gi, "[REDACTED]")
    .replace(/[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}/gi, "[EMAIL]")
    .replace(/\b[a-z0-9_-]{40,}\b/gi, "[REDACTED]")
    .replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, max);
}

/** An allowlist, not a recursive pass-through of untrusted page payloads. */
export function sanitizeCaptureEvent(payload, timestamp = Date.now()) {
  if (!payload || !["click", "change", "submit", "navigation"].includes(payload.type)) return null;
  const event = { type: payload.type, ts: timestamp, url: sanitizeUrl(payload.url) };
  const target = payload.target;
  if (target && typeof target === "object") {
    event.target = {};
    for (const key of ["tag", "role", "id", "type", "dataTab", "dataTicket"]) {
      if (target[key]) event.target[key] = sanitizeText(target[key], 120);
    }
    if (["button", "a"].includes(target.tag) && target.label) event.target.label = sanitizeText(target.label);
    if (target.href) event.target.href = sanitizeUrl(target.href);
  }
  return event;
}
