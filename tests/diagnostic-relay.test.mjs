import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { pantherDiagnosticRelay } from "../extension/shared/capture.js";

function target() {
  const listeners = new Map();
  return {
    addEventListener(type, listener) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    dispatchEvent(event) { for (const listener of [...(listeners.get(event.type) || [])]) listener(event); },
    count(type) { return listeners.get(type)?.size || 0; }
  };
}

function fixture() {
  const document = target(), window = target(), failures = [];
  document.addEventListener("panther-diagnostic", (event) => failures.push(event.detail));
  const context = vm.createContext({ document,
    addEventListener: window.addEventListener, removeEventListener: window.removeEventListener,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }
  });
  return { document, window, failures,
    command(nonce, action = "start", startedAt = 1000) {
      context.args = [nonce, action, startedAt];
      vm.runInContext(`(${pantherDiagnosticRelay.toString()})(...args)`, context);
    },
    fail() { window.dispatchEvent({ type: "error", message: "synthetic failure", filename: "https://example.test/app.js" }); }
  };
}

test("relay Stop removes listeners and a delayed Start cannot revive the stopped nonce", () => {
  const f = fixture(); f.command("first"); f.command("first"); f.fail();
  assert.equal(f.failures.length, 1);
  f.document.dispatchEvent({ type: "panther-diagnostic-stop", detail: { nonce: "first" } });
  assert.equal(f.window.count("error"), 0);
  assert.equal(f.window.count("unhandledrejection"), 0);
  assert.equal(f.document.count("panther-diagnostic-stop"), 0);
  f.command("first"); f.fail();
  assert.equal(f.failures.length, 1);
});

test("replacement keeps one listener pair and stale commands cannot replace or stop it", () => {
  const f = fixture(); f.command("first"); f.command("second", "start", 2000);
  f.command("first", "stop", 1000); f.command("first", "start", 1000);
  f.document.dispatchEvent({ type: "panther-diagnostic-stop", detail: { nonce: "first" } });
  f.fail();
  assert.equal(f.window.count("error"), 1);
  assert.equal(f.window.count("unhandledrejection"), 1);
  assert.equal(f.failures.length, 1);
  assert.equal(f.failures[0].nonce, "second");
  f.command("second", "stop", 2000); f.fail();
  assert.equal(f.failures.length, 1);
});

test("Stop received before injection leaves a tombstone that refuses late installation", () => {
  const f = fixture(); f.command("first", "stop", 1000); f.command("first", "start", 1000); f.fail();
  assert.equal(f.failures.length, 0);
  assert.equal(f.window.count("error"), 0);
  f.command("second", "start", 2000); f.fail();
  assert.equal(f.failures.length, 1);
});

test("different sessions with identical start times cannot silently supersede one another", () => {
  const f = fixture(); f.command("first"); f.command("ambiguous", "start", 1000);
  f.command("ambiguous", "stop", 1000); f.fail();
  assert.equal(f.failures.length, 1);
  assert.equal(f.failures[0].nonce, "first");
});
