import test from "node:test";
import assert from "node:assert/strict";
import { createMonitorWindowController } from "../extension/shared/monitor-window.js";

const url = "chrome-extension://test-extension/monitor/monitor.html";
function fixture() {
  const calls = { contexts: [], create: [], update: [] };
  let contexts = [], nextId = 10;
  const removed = [];
  const api = {
    runtime: { getURL: (path) => `chrome-extension://test-extension/${path}`,
      getContexts: async (filter) => { calls.contexts.push(filter); return structuredClone(contexts); } },
    windows: {
      onRemoved: { addListener: (callback) => removed.push(callback) },
      get: async (id) => ({ id, tabs: [{ pendingUrl: url }] }),
      create: async (options) => { calls.create.push(options); const id = nextId++; contexts.push({ windowId: id, documentUrl: options.url }); return { id }; },
      update: async (id, options) => { calls.update.push({ id, ...options }); return { id }; }
    }
  };
  return { api, calls, controller: createMonitorWindowController(api),
    setContexts(value) { contexts = value; }, remove(id) { contexts = contexts.filter((item) => item.windowId !== id); removed.forEach((callback) => callback(id)); } };
}

test("manager initialization does not inspect windows, create UI or request any backend authority", () => {
  const f = fixture();
  assert.deepEqual(f.controller.snapshot(), { monitorError: "" });
  assert.deepEqual(f.calls, { contexts: [], create: [], update: [] });
});

test("concurrent explicit opens create one small window and later opens focus it", async () => {
  const f = fixture();
  const first = f.controller.open(), second = f.controller.open();
  assert.equal(first, second);
  assert.equal((await first).windowId, 10);
  assert.equal((await second).monitorError, "");
  assert.deepEqual(f.calls.create, [{ url, type: "popup", width: 400, height: 620, focused: true }]);
  assert.deepEqual(f.calls.contexts, [{ documentUrls: [url] }]);
  assert.equal((await f.controller.open()).windowId, 10);
  assert.equal(f.calls.create.length, 1);
  assert.deepEqual(f.calls.update, [{ id: 10, focused: true, state: "normal" }]);
});

test("worker recreation reuses an exact existing monitor context without opening or starting anything on its own", async () => {
  const f = fixture();
  f.setContexts([{ windowId: 2, documentUrl: "https://example.test/monitor/monitor.html" },
    { windowId: 3, documentUrl: `${url}/attacker` }, { windowId: -1, documentUrl: url }, { windowId: 12, documentUrl: url }]);
  const recreated = createMonitorWindowController(f.api);
  assert.equal(f.calls.contexts.length, 0);
  assert.equal((await recreated.open()).windowId, 12);
  assert.equal(f.calls.create.length, 0);
  assert.equal(f.calls.update[0].id, 12);
});

test("sequential opens during document loading validate the created window instead of duplicating it", async () => {
  const f = fixture();
  assert.equal((await f.controller.open()).windowId, 10);
  f.setContexts([]); // Creation resolved but the document is not registered yet.
  assert.equal((await f.controller.open()).windowId, 10);
  assert.equal(f.calls.create.length, 1);
  f.api.windows.get = async () => ({ tabs: [{}] });
  assert.match((await f.controller.open()).monitorError, /not yet identifiable/);
  assert.equal(f.calls.create.length, 1);
  f.remove(10);
  assert.equal((await f.controller.open()).windowId, 11);
  assert.equal(f.calls.create.length, 2);
});

test("a created-window lookup error fails closed without duplicating or touching power", async () => {
  const f = fixture(); await f.controller.open(); f.setContexts([]);
  f.api.windows.get = async () => { throw new Error("Window identity unavailable"); };
  assert.match((await f.controller.open()).monitorError, /identity unavailable/);
  assert.equal(f.calls.create.length, 1);
});

test("close during focus rechecks identity and creates one replacement only after the old context is gone", async () => {
  const f = fixture();
  f.setContexts([{ windowId: 4, documentUrl: url }]);
  f.api.windows.update = async () => { f.setContexts([]); throw new Error("Window closed"); };
  assert.equal((await f.controller.open()).ok, true);
  assert.equal(f.calls.contexts.length, 2);
  assert.equal(f.calls.create.length, 1);
});

test("focus failure while monitor still exists reports separately and never duplicates the window", async () => {
  const f = fixture(); f.setContexts([{ windowId: 4, documentUrl: url }]);
  f.api.windows.update = async () => { throw new Error("Cannot focus window"); };
  const result = await f.controller.open();
  assert.equal(result.ok, false);
  assert.match(result.monitorError, /Cannot focus window/);
  assert.equal(f.calls.create.length, 0);
  assert.equal(f.controller.snapshot().monitorError, result.monitorError);
  f.api.windows.update = async (id) => ({ id });
  assert.equal((await f.controller.open()).monitorError, "");
});

test("creation or getContexts failures remain visible, retryable window errors", async () => {
  const f = fixture(); f.api.windows.create = async () => { throw new Error("Window denied"); };
  assert.match((await f.controller.open()).monitorError, /Window denied/);
  f.api.runtime.getContexts = undefined;
  assert.equal((await f.controller.open()).ok, false);
  assert.match(f.controller.snapshot().monitorError, /toolbar popup/);
});

test("unconfirmed window creation is not reported as success", async () => {
  const f = fixture(); f.api.windows.create = async () => undefined;
  assert.match((await f.controller.open()).monitorError, /did not confirm/);
});
