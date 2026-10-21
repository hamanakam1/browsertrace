import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import { createLifecycleController, NATIVE_HOST, trustedExtensionControl } from "../extension/shared/lifecycle.js";

const control = { id: "test-extension", url: "chrome-extension://test-extension/popup/popup.html" };
const flush = () => new Promise((resolve) => setImmediate(resolve));
const backgroundSource = (await readFile(new URL("../extension/background/background.js", import.meta.url), "utf8")).replace(/^import .*;\n/gm, "");
class Event {
  listeners = [];
  addListener(fn) { this.listeners.push(fn); }
  emit(value) { for (const fn of this.listeners) fn(value); }
}

function fixture({ automatic = true, timeoutMs = 1000, connectError = "", persistenceTimeoutMs = 10 } = {}) {
  const calls = { connect: [], freeze: [], unlock: 0 };
  const ports = [];
  const api = { runtime: {
    id: control.id, getURL: (path) => `chrome-extension://${control.id}/${path}`,
    connectNative(name) {
      calls.connect.push(name);
      if (connectError) throw new Error(connectError);
      const port = { onMessage: new Event(), onDisconnect: new Event(), posts: [], disconnected: false,
        postMessage(message) {
          this.posts.push(message);
          if (automatic) queueMicrotask(() => this.respond(message.action, message.action === "stop" ? "off" : "on"));
        },
        respond(action, state, fields = {}) {
          const message = [...this.posts].reverse().find((item) => item.action === action);
          this.onMessage.emit({ id: message.id, ok: true, state, ...fields });
        },
        disconnect() { this.disconnected = true; this.onDisconnect.emit(); }
      };
      ports.push(port);
      return port;
    }
  } };
  const capture = { freeze: async (reason) => { calls.freeze.push(reason); }, unlock: () => { calls.unlock++; } };
  const lifecycle = createLifecycleController(api, capture, { timeoutMs, persistenceTimeoutMs });
  return { api, capture, calls, ports, lifecycle, send: (type, sender = control) => lifecycle.message({ type }, sender) };
}

test("worker initialization and repeated Off/status checks never connect or start the native host", async () => {
  const f = fixture();
  assert.equal(f.lifecycle.snapshot().power.state, "off");
  assert.equal(f.calls.freeze.length, 1);
  await f.send("extension.getPowerState");
  await f.send("extension.turnOff");
  await f.send("extension.getPowerState");
  assert.equal(f.calls.connect.length, 0);
  assert.equal(f.lifecycle.isOn(), false);
  assert.equal(f.lifecycle.snapshot().power.error, "");
});

test("only exact trusted extension popup/options URLs can start or stop the helper", async () => {
  const f = fixture();
  for (const sender of [
    { ...control, url: "https://app.test/", tab: { id: 7 } },
    { ...control, url: `chrome-extension://${control.id}.attacker/popup/popup.html` },
    { ...control, url: `chrome-extension://attacker@${control.id}/popup/popup.html` },
    { ...control, url: `chrome-extension://${control.id}/content/content.js` },
    { ...control, id: "other-extension" }
  ]) assert.equal((await f.send("extension.turnOn", sender)).ok, false);
  assert.equal(f.calls.connect.length, 0);
  assert.equal((await f.send("extension.turnOn", { ...control, tab: { id: 99 }, url: `chrome-extension://${control.id}/options/options.html?recording=fixture` })).power.state, "on");
  await f.send("extension.turnOff");
});

test("explicit On owns one persistent native port and status uses that port without spawning another", async () => {
  const f = fixture();
  const on = await f.send("extension.turnOn");
  assert.equal(on.power.state, "on");
  assert.equal(on.power.configured, true);
  assert.equal(f.lifecycle.isOn(), true);
  assert.deepEqual(f.calls.connect, [NATIVE_HOST]);
  assert.equal(f.calls.unlock, 1);
  await f.send("extension.getPowerState");
  await f.send("extension.turnOn");
  assert.equal(f.calls.connect.length, 1);
  assert.deepEqual(f.ports[0].posts.map((message) => message.action), ["start", "status"]);
  for (const frame of f.ports[0].posts) assert.deepEqual(Object.keys(frame).sort(), ["action", "id"]);
  assert.equal((await f.send("extension.turnOff")).power.state, "off");
  assert.equal(f.lifecycle.snapshot().power.error, "");
  assert.equal(f.ports[0].disconnected, true);
});

test("missing registration reports actionable setup and never silently falls back to external HTTP", async () => {
  const f = fixture({ connectError: "Specified native messaging host not found." });
  const result = await f.send("extension.turnOn");
  assert.equal(result.ok, false);
  assert.equal(result.power.state, "error");
  assert.equal(result.power.configured, false);
  assert.match(result.power.error, /Install\/register com.panther.intent_capture/);
  assert.match(result.power.error, /test-extension/);
  assert.equal(f.calls.unlock, 0);
  await f.send("extension.getPowerState");
  assert.equal(f.calls.connect.length, 1);
});

test("an unsupported preview does not attempt a native connection", async () => {
  const f = fixture(); f.api.runtime.getManifest = () => ({ name: "Panther Firefox preview" });
  assert.match((await f.send("extension.turnOn")).power.error, /unsupported preview/);
  assert.equal(f.calls.connect.length, 0);
});

test("duplicate On while starting shares one request and requires confirmed on before unlocking", async () => {
  const f = fixture({ automatic: false });
  const first = f.send("extension.turnOn"), second = f.send("extension.turnOn");
  await flush();
  assert.equal(f.lifecycle.snapshot().power.state, "starting");
  assert.equal(f.calls.unlock, 0);
  assert.equal(f.calls.connect.length, 1);
  f.ports[0].respond("start", "on");
  assert.equal((await first).ok, true); assert.equal((await second).ok, true);
  const off = f.send("extension.turnOff"); await flush(); f.ports[0].respond("stop", "off"); await off;
});

test("Off during native startup freezes immediately and a late on response cannot unlock capture", async () => {
  const f = fixture({ automatic: false });
  const starting = f.send("extension.turnOn"); await flush();
  const freezes = f.calls.freeze.length;
  const stopping = f.send("extension.turnOff");
  assert.equal(f.lifecycle.snapshot().power.state, "stopping");
  assert.equal(f.calls.freeze.length, freezes + 1);
  assert.equal(f.lifecycle.isOn(), false);
  assert.equal((await f.send("extension.turnOn")).ok, false);
  await flush();
  f.ports[0].respond("start", "on");
  assert.equal((await starting).ok, false);
  assert.equal(f.calls.unlock, 0);
  f.ports[0].respond("stop", "off");
  assert.equal((await stopping).power.state, "off");
});

test("Off before an awaited On connects prevents even a native process from being launched", async () => {
  const f = fixture();
  const starting = f.send("extension.turnOn");
  const stopping = f.send("extension.turnOff");
  await Promise.all([starting, stopping]);
  assert.equal(f.calls.connect.length, 0);
  assert.equal(f.calls.unlock, 0);
  assert.equal(f.lifecycle.snapshot().power.state, "off");
});

test("unexpected native disconnect freezes capture, preserves uncertainty and never reconnects automatically", async () => {
  const f = fixture(); await f.send("extension.turnOn");
  f.api.runtime.lastError = { message: "Native host exited." };
  f.ports[0].onDisconnect.emit();
  assert.equal(f.lifecycle.isOn(), false);
  assert.equal(f.lifecycle.snapshot().power.state, "error");
  assert.match(f.lifecycle.snapshot().power.error, /in-flight delivery are not yet verified/);
  await f.send("extension.getPowerState"); await flush();
  assert.equal(f.calls.connect.length, 1);
  const off = await f.send("extension.turnOff");
  assert.equal(off.ok, false);
  assert.equal(off.power.state, "error");
  assert.match(off.power.error, /prior backend shutdown remains unconfirmed/);
  assert.equal(f.calls.connect.length, 1);
});

test("a stale old-port disconnect cannot disable a later explicitly started connection", async () => {
  const f = fixture(); await f.send("extension.turnOn"); const old = f.ports[0];
  await f.send("extension.turnOff"); await f.send("extension.turnOn");
  old.onDisconnect.emit();
  assert.equal(f.lifecycle.isOn(), true);
  assert.equal(f.lifecycle.snapshot().power.state, "on");
  assert.equal(f.calls.connect.length, 2);
  await f.send("extension.turnOff");
});

test("a failed native stop never reports confirmed Off or undoes an already accepted delivery", async () => {
  const f = fixture({ automatic: false });
  const starting = f.send("extension.turnOn"); await flush(); f.ports[0].respond("start", "on"); await starting;
  const stopping = f.send("extension.turnOff"); await flush();
  f.ports[0].respond("stop", "error", { ok: false, error: "Child shutdown timeout" });
  const result = await stopping;
  assert.equal(result.ok, false); assert.equal(result.power.state, "error");
  assert.match(result.power.error, /shutdown is unconfirmed/);
  assert.equal(f.lifecycle.isOn(), false);
});

test("status detects an exited owned child without probing or adopting another backend", async () => {
  const f = fixture({ automatic: false });
  const starting = f.send("extension.turnOn"); await flush(); f.ports[0].respond("start", "on"); await starting;
  const checking = f.send("extension.getPowerState"); await flush(); f.ports[0].respond("status", "off");
  assert.equal((await checking).power.state, "error");
  assert.equal(f.calls.connect.length, 1);
  assert.equal(f.ports[0].disconnected, true);
});

test("malformed native responses fail closed and a stalled helper times out without reconnect", async () => {
  const malformed = fixture({ automatic: false });
  const starting = malformed.send("extension.turnOn"); await flush();
  malformed.ports[0].respond("start", "surprise");
  assert.equal((await starting).power.state, "error"); assert.equal(malformed.calls.unlock, 0);
  const stalled = fixture({ automatic: false, timeoutMs: 10 });
  assert.match((await stalled.send("extension.turnOn")).power.error, /timed out/);
  assert.equal(stalled.calls.connect.length, 1);
  assert.equal(stalled.ports[0].disconnected, true);
});

test("native stop proceeds immediately despite stalled local persistence and reports the remaining uncertainty", async () => {
  const f = fixture(); await f.send("extension.turnOn");
  let resolvePersistence;
  f.capture.freeze = () => new Promise((resolve) => { resolvePersistence = resolve; });
  const stopping = f.send("extension.turnOff");
  assert.equal(f.lifecycle.isOn(), false);
  assert.equal(f.ports[0].posts.at(-1).action, "stop");
  const result = await stopping;
  assert.equal(result.power.state, "off");
  assert.match(result.power.error, /persistence is still pending/);
  assert.equal(f.ports[0].disconnected, true);
  resolvePersistence();
  await flush();
  assert.equal(f.lifecycle.snapshot().power.error, "");
});

function backgroundFixture() {
  const f = fixture();
  Object.assign(f.api.runtime, { onMessage: new Event(), onStartup: new Event(), onInstalled: new Event() });
  f.api.tabs = { onUpdated: new Event(), onRemoved: new Event() };
  f.api.alarms = { onAlarm: new Event() };
  Object.assign(f.capture, { message: async () => ({ ok: true, status: "idle", enabled: false, session: null, result: null }),
    tabUpdated: async () => {}, tabRemoved: async () => {}, browserStarted: async () => {}, alarm: async () => {} });
  let powered;
  vm.runInNewContext(backgroundSource, { chrome: f.api, fetch: () => { throw new Error("Unexpected HTTP request"); }, Date, console,
    createCaptureController: (_api, _fetch, _now, options) => { powered = options.isPowered; return f.capture; }, createLifecycleController, trustedExtensionControl });
  return { ...f, powered: () => powered(), background: (type, sender = control) => new Promise((resolve) => f.api.runtime.onMessage.listeners[0]({ type }, sender, resolve)) };
}

test("actual background getStatus checks only the existing native lease and detects a dead owned child", async () => {
  const f = backgroundFixture();
  assert.equal((await f.background("extension.getStatus")).power.state, "off");
  assert.equal(f.calls.connect.length, 0);
  assert.equal((await f.background("extension.turnOn")).power.state, "on");
  assert.equal(f.powered(), true);
  const originalRespond = f.ports[0].respond.bind(f.ports[0]);
  f.ports[0].respond = (action, state, fields) => originalRespond(action, action === "status" ? "off" : state, fields);
  const result = await f.background("extension.getStatus");
  assert.equal(result.power.state, "error");
  assert.match(result.power.error, /no longer On/);
  assert.equal(f.powered(), false);
  assert.equal(f.calls.connect.length, 1);
  assert.deepEqual(f.ports[0].posts.map((frame) => frame.action), ["start", "status"]);
});

test("actual background startup, alarms, page status and spoofed controls cannot launch a helper", async () => {
  const f = backgroundFixture();
  f.api.runtime.onStartup.emit(); f.api.runtime.onInstalled.emit(); f.api.alarms.onAlarm.emit({ name: "panther.automation" });
  const page = { id: control.id, url: "https://page.test/", tab: { id: 7 }, frameId: 0 };
  await f.background("page.getStatus", page);
  assert.equal((await f.background("extension.turnOn", page)).ok, false);
  await f.background("extension.getStatus");
  assert.equal(f.calls.connect.length, 0);
});
