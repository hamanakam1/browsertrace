import test from "node:test";
import assert from "node:assert/strict";
import { sanitizeEventPayload, sanitizeCaptureEvent, sanitizeText, sanitizeUrl } from "../extension/shared/redaction.js";

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
  const out = sanitizeUrl("http://user:pass@127.0.0.1:8080/?token=secret#cockpit");
  assert.equal(out, "http://127.0.0.1:8080/#cockpit");
});
test("page failures keep only allowlisted bounded fields", () => {
  const out = sanitizeCaptureEvent({ type: "diagnostic", ts: 100, url: "https://app.test/page?token=private",
    diagnostic: { kind: "http_error", message: "A request returned HTTP 500", source: "https://app.test/api/tickets?token=private", status: 500, private: "never" } }, 100);
  assert.deepEqual(out.diagnostic, { kind: "http_error", message: "A request returned HTTP 500", source: "https://app.test/api/tickets", status: 500 });
  assert.equal(out.url, "https://app.test/page");
  assert.equal(out.ts, 100);
  assert.doesNotMatch(JSON.stringify(out), /private|never/);
});
test("page failures are dropped when unknown, empty or out of range", () => {
  for (const diagnostic of [
    { kind: "unknown_kind", message: "x" },
    { kind: "http_error" },
    { kind: "http_error", message: "x", status: 200 },
    { message: "no kind" }
  ]) {
    assert.equal(sanitizeCaptureEvent({ type: "diagnostic", ts: 1, diagnostic }, 1), null);
  }
  assert.equal(sanitizeCaptureEvent({ type: "diagnostic", ts: 1, diagnostic: { kind: "http_error", message: "x", status: 500 } }, 1).diagnostic.status, 500);
  // A field that does not belong to the kind is dropped, not carried.
  const extra = sanitizeCaptureEvent({ type: "diagnostic", ts: 1, diagnostic: { kind: "http_error", message: "x", status: 500, line: 4 } }, 1);
  assert.equal(extra.diagnostic.line, undefined);
  const position = sanitizeCaptureEvent({ type: "diagnostic", ts: 1, diagnostic: { kind: "uncaught_exception", message: "boom", line: 12, column: 3 } }, 1);
  assert.equal(position.diagnostic.line, 12);
  assert.equal(position.diagnostic.column, 3);
});
test("page failure messages are scrubbed like every other captured text", () => {
  const out = sanitizeCaptureEvent({ type: "diagnostic", ts: 1, diagnostic: { kind: "uncaught_exception", message: "failed for user@example.test with Bearer abcdef123456" } }, 1);
  assert.doesNotMatch(out.diagnostic.message, /user@example\.test|abcdef123456/);
});

test("captured text removes embedded URL credentials and quoted credential values before clipping", () => {
  for (const text of [
    'Failed https://synthetic-user:synthetic-pass@app.test/reset?code=synthetic-code&access_token=synthetic-token',
    'JSON failed: {"password":"synthetic-password","access_token":"synthetic-token"}',
    "Request failed: {'session_id': 'synthetic session with spaces', 'cookie': 'synthetic-cookie'}",
    'Request failed: password="synthetic password with spaces"',
    String.raw`JSON failed: {"password":"synthetic \"quoted\" value","status":500}`,
    'Basic c3ludGhldGljOmNyZWRlbnRpYWw=',
    'Request failed: {"secret":"synthetic value without closing quote'
  ]) {
    assert.doesNotMatch(sanitizeText(text, 300), /synthetic/);
  }
  assert.equal(sanitizeText('Failed https://app.test/api/items?code=synthetic-code', 300), 'Failed https://app.test/api/items');
  assert.match(sanitizeText('JSON failed: {"password":"synthetic-password","status":500}', 300), /"status":500/);
  assert.equal(sanitizeText('Basic c3ludGhldGljOmNyZWRlbnRpYWw='), '[REDACTED]');
});

test("text redaction omits oversized source values before applying its output limit", () => {
  const boundary = "safe ".repeat(1638) + "ok";
  assert.equal(boundary.length, 8192);
  assert.equal(sanitizeText(boundary, 120), boundary.slice(0, 120));
  const astralBoundary = "safe ".repeat(24) + "😀".repeat(4036);
  assert.equal(astralBoundary.length, 8192);
  assert.equal(sanitizeText(astralBoundary), astralBoundary.slice(0, 120));
  for (const oversized of ["a".repeat(8193), "safe ".repeat(14000), "safe ".repeat(24) + "😀".repeat(4037)]) {
    assert.equal(sanitizeText(oversized, 120), "");
    assert.equal(sanitizeText(oversized, 1000), "");
  }
  for (const value of [null, undefined, 42, true, ["private"], { toString() { throw new Error("Untrusted coercion"); } }]) {
    assert.equal(sanitizeText(value), "");
  }
});

test("direct capture metadata and diagnostic text cannot bypass the source limit", () => {
  const oversized = "safe ".repeat(1639);
  const target = { tag: "button", label: oversized };
  for (const key of ["role", "id", "type", "dataTab", "dataTicket"]) target[key] = oversized;
  const clean = sanitizeCaptureEvent({ type: "click", target }, 1);
  assert.equal(clean.target.tag, "button");
  for (const key of ["role", "id", "type", "dataTab", "dataTicket", "label"]) assert.equal(clean.target[key], "");
  const diagnostic = sanitizeCaptureEvent({ type: "diagnostic", diagnostic: { kind: "http_error", status: 500, message: oversized } }, 1);
  assert.deepEqual(diagnostic.diagnostic, { kind: "http_error", status: 500 });
  assert.equal(sanitizeCaptureEvent({ type: "diagnostic", diagnostic: { kind: "unhandled_rejection", message: oversized } }, 1), null);
});
