export const BACKEND_ORIGIN = "http://127.0.0.1:7890";
export const STORAGE_ROOT_KEY = "extension.local";

export const REDACT_FIELD_NAMES = new Set([
  "password",
  "passwd",
  "pwd",
  "secret",
  "apikey",
  "api_key",
  "access_key",
  "secret_key",
  "token",
  "authorization",
  "credential",
  "private_key",
  "session_id",
  "auth",
  "cookie"
]);

export const REDACT_FIELD_IDS = new Set([
  "password",
  "passwd",
  "secret",
  "apikey",
  "api_key",
  "access_key",
  "secret_key",
  "token",
  "credential",
  "private_key",
  "session_id"
]);

/**
 * Page-failure channels Panther can observe without reading page text, typed
 * values or keystrokes. The backend mirrors this set exactly; a new kind needs
 * a matching backend change and a redaction-parity update.
 *
 * - uncaught_exception: the page threw an uncaught error.
 * - unhandled_rejection: the page left a promise rejection unhandled.
 * - http_error: a request the page made returned an HTTP error status.
 * - resource_error: a subresource the page loaded failed to load.
 */
export const DIAGNOSTIC_KINDS = new Set([
  "uncaught_exception",
  "unhandled_rejection",
  "http_error",
  "resource_error"
]);
