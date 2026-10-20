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
