import { REDACT_FIELD_NAMES, REDACT_FIELD_IDS } from "./constants.js";

function looksSecret(key) {
  const k = String(key).toLowerCase();
  return REDACT_FIELD_NAMES.has(k) || REDACT_FIELD_IDS.has(k) || /secret|token|key|pass|cred|auth|cookie/.test(k);
}

function scrubValue(value) {
  if (typeof value === "string") return "***REDACTED***";
  if (typeof value === "number") return 0;
  if (typeof value === "boolean") return false;
  if (Array.isArray(value)) return value.map(() => "***REDACTED***");
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
