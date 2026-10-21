import test from "node:test";
import assert from "node:assert/strict";
import { sanitizeEventPayload, sanitizeCaptureEvent, sanitizeUrl } from "../extension/shared/redaction.js";

test("redacts secret field names and nested fields", () => {
  const out = sanitizeEventPayload({ password: "hunter2", token: "abc", user: { api_key: "sk-123", email: "a@b.c" } });
  assert.equal(out.password, "***REDACTED***");
  assert.equal(out.token, "***REDACTED***");
  assert.equal(out.user.api_key, "***REDACTED***");
});
test("capture allowlist excludes typed keys and input values", () => {
  const out = sanitizeCaptureEvent({ type: "change", ts: 100, key: "a", value: "private", target: { tag: "input", value: "private", label: "private" } }, 100);
  assert.ok(out);
  assert.equal(JSON.stringify(out).includes("private"), false);
  assert.equal(out.key, undefined);
});
test("URLs retain dashboard routes and discard credentials and query", () => {
  const out = sanitizeUrl("http://user:pass@127.0.0.1:7878/?token=secret#cockpit");
  assert.equal(out, "http://127.0.0.1:7878/#cockpit");
});
