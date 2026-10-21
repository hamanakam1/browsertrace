import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createCaptureController, SESSION_KEY, RESULT_KEY, SETTINGS_KEY, BROWSER_KEY, MAX_EVENTS } from "../extension/shared/capture.js";
import { sanitizeCaptureEvent, sanitizeText, sanitizeUrl } from "../extension/shared/redaction.js";

const control = { id: "test-extension", url: "chrome-extension://test-extension/popup/popup.html" };
const page = { id: "test-extension", tab: { id: 7 }, frameId: 0, url: "https://app.test/dashboard" };
const clone = (value) => value === undefined ? undefined : structuredClone(value);

function fixture(options = {}) {
  const data = { [SETTINGS_KEY]: { enabled: true }, ...clone(options.data || {}) };
  const sessionData = {};
  const calls = { inject: [], messages: [], captures: [], fetch: [], badges: [], tabs: [] };
  const activeTab = { id: 7, windowId: 42, url: "https://app.test/dashboard?token=private#work-board", title: "Dashboard" };
  let timestamp = 1700000000000;
  const api = {
    runtime: { id: control.id, getURL: (path) => `chrome-extension://${control.id}/${path}`, getManifest: () => ({ name: "Panther" }) },
    storage: { session: {
      get: async (key) => ({ [key]: clone(sessionData[key]) }),
      set: async (patch) => { Object.assign(sessionData, clone(patch)); }
    }, local: {
      get: async (key) => ({ [key]: clone(data[key]) }),
      set: async (patch) => { for (const [key, value] of Object.entries(patch)) data[key] = clone(value); },
      remove: async (key) => { delete data[key]; }
    } },
    action: { setBadgeText: async (value) => calls.badges.push(value), setBadgeBackgroundColor: async () => {} },
    tabs: {
      query: async () => [clone(activeTab)],
      sendMessage: async (...args) => { calls.messages.push(args); return { ok: true }; },
      captureVisibleTab: async (...args) => { calls.captures.push(args); return "data:image/jpeg;base64,/9j/2Q=="; },
      create: async (tab) => { calls.tabs.push(tab); }
    },
    scripting: { executeScript: async (value) => { calls.inject.push(value); } }
  };
  let fetchBehavior = options.fetch || (async (url) => ({ ok: true, json: async () => url.endsWith("/record") ? { id: "record-1" } : { tickets: [{ title: "Review observed intent" }] } }));
  const fetcher = async (url, init) => { calls.fetch.push({ url, init }); return fetchBehavior(url, init); };
  const makeController = () => createCaptureController(api, fetcher, () => timestamp++);
  let controller = makeController();
  return {
    api, data, sessionData, calls, activeTab,
    get controller() { return controller; },
    restartWorker() { controller = makeController(); return controller; },
    setFetch(fn) { fetchBehavior = fn; },
    advance(ms) { timestamp += ms; },
    send(type, values = {}, sender = control) { return controller.message({ type, ...values }, sender); },
    start(values = {}) { return this.send("extension.startRecording", { tabId: 7, consent: true, ...values }); },
    event(values = {}, sender = page) { return this.send("page.interaction", { sessionId: data[SESSION_KEY]?.sessionId, payload: { type: "click", url: activeTab.url, target: { tag: "button", label: "Save" }, ...values } }, sender); }
  };
}

test("all manifests are valid and Chromium permissions are scoped to an invoked tab", async () => {
  for (const variant of ["", ".chrome", ".firefox", ".safari"]) {
    const manifest = JSON.parse(await readFile(new URL(`../extension/manifest${variant}.json`, import.meta.url)));
    assert.deepEqual(manifest.permissions, ["storage", "activeTab", "scripting"]);
    assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1:7890/*"]);
    assert.equal(manifest.content_scripts, undefined);
    assert.equal(manifest.offscreen, undefined);
    if (!variant || variant === ".chrome") assert.equal(manifest.minimum_chrome_version, "116");
  }
});

test("worker requires stored consent, fresh start consent, and the active ordinary webpage", async () => {
  const f = fixture({ data: { [SETTINGS_KEY]: { enabled: false } } });
  await assert.rejects(f.start(), /consent/);
  assert.equal(f.calls.inject.length, 0);
  await f.send("extension.setEnabled", { enabled: true });
  await assert.rejects(f.start({ consent: false }), /consent/);
  await assert.rejects(f.start({ tabId: 9 }), /active tab/);
  f.activeTab.url = "chrome://settings";
  await assert.rejects(f.start(), /ordinary http/);
  f.activeTab.url = "https://app.test";
  await assert.rejects(f.start({ video: true }), /not supported/);
  f.api.runtime.getManifest = () => ({ name: "Panther (Firefox preview)" });
  await assert.rejects(f.start(), /unsupported preview/);
});

test("popup tabId works without sender.tab and only the top frame is injected", async () => {
  const f = fixture();
  const result = await f.start({ goal: "Make filtering clearer" });
  assert.equal(result.status, "recording");
  assert.deepEqual(f.calls.inject[0], { target: { tabId: 7, frameIds: [0] }, files: ["content/content.js"] });
  assert.equal(f.data[SESSION_KEY].url, "https://app.test/dashboard#work-board");
  assert.equal(f.data[SESSION_KEY].goal, "Make filtering clearer");
  assert.equal(f.data[SESSION_KEY].screenshotConsent, false);
  assert.ok(f.calls.badges.some((value) => value.text === "REC"));
});

test("page senders cannot invoke extension controls or impersonate another tab/frame/origin", async () => {
  const f = fixture();
  const rejected = await f.send("extension.startRecording", { tabId: 7, consent: true }, page);
  assert.equal(rejected.ok, false);
  await f.start();
  for (const sender of [{ ...page, tab: { id: 8 } }, { ...page, frameId: 1 }, { ...page, url: "https://other.test" }, { ...page, id: "other-extension" }]) {
    assert.equal((await f.event({}, sender)).ignored, true);
  }
  assert.equal((await f.send("page.interaction", { sessionId: "stale-session", payload: { type: "click" } }, page)).ignored, true);
  assert.equal(f.data[SESSION_KEY].events.length, 0);
});

test("known extension popup and options pages can control capture in tabs or windows without URL-prefix spoofing", async () => {
  const f = fixture();
  for (const path of ["popup/popup.html", "options/options.html?recording=rec"]) {
    const sender = { ...control, tab: { id: 99 }, url: `chrome-extension://${control.id}/${path}` };
    assert.equal((await f.send("extension.getStatus", {}, sender)).ok, true);
  }
  for (const url of [
    `chrome-extension://${control.id}.attacker/popup/popup.html`,
    `chrome-extension://${control.id}/popup/popup.html/extra`,
    `chrome-extension://${control.id}/background/background.js`,
    `chrome-extension://attacker@${control.id}/popup/popup.html`,
    `https://${control.id}/popup/popup.html`
  ]) assert.equal((await f.send("extension.getStatus", {}, { ...control, url })).ok, false);
});

test("allowlisted event metadata drops input values, keys, arbitrary bodies and secret URLs", async () => {
  const f = fixture();
  await f.start();
  await f.event({ value: "typed-private-value", key: "p", password: "secret", body: "private body", target: { tag: "input", value: "private", label: "typed private", type: "password", id: "login" }, url: "https://user:pass@app.test/?api_key=secret#access_token=abc" });
  const serialized = JSON.stringify(f.data[SESSION_KEY].events);
  for (const secret of ["typed-private-value", "typed private", "private body", "api_key", "access_token", "user:pass"]) assert.ok(!serialized.includes(secret));
  assert.deepEqual(f.data[SESSION_KEY].events[0].target, { tag: "input", id: "login", type: "password" });
  assert.equal(f.calls.captures.length, 0);
});

test("captures dashboard semantic references and preserves safe routes", () => {
  const out = sanitizeCaptureEvent({ type: "click", url: "http://127.0.0.1:7878/#master", target: { tag: "button", dataTab: "master", dataTicket: "M-12345", label: "Open" } }, 123);
  assert.equal(out.target.dataTab, "master");
  assert.equal(out.target.dataTicket, "M-12345");
  assert.equal(out.url, "http://127.0.0.1:7878/#master");
  assert.equal(out.ts, 123);
  assert.equal(sanitizeCaptureEvent({ type: "keydown", key: "x" }), null);
  assert.equal(sanitizeUrl("https://app.test/reset/token/secret-value?query=secret#token=secret"), "https://app.test/reset/token/REDACTED");
  assert.equal(sanitizeUrl("javascript:alert(1)"), "");
  assert.equal(sanitizeUrl("https://app.test/%74oken/private#token/private"), "https://app.test/%74oken/REDACTED");
  assert.equal(sanitizeText("password=hunter2 hi person@example.com"), "[REDACTED] hi [EMAIL]");
});

test("event writes are serialized and survive worker recreation", async () => {
  const f = fixture();
  await f.start();
  await Promise.all(Array.from({ length: 50 }, (_, i) => f.event({ target: { tag: "button", label: `Action ${i}` } })));
  assert.equal(f.data[SESSION_KEY].events.length, 50);
  f.restartWorker();
  assert.equal((await f.send("extension.getStatus")).session.eventCount, 50);
  await f.event();
  assert.equal(f.data[SESSION_KEY].events.length, 51);
});

test("inject failures leave no phantom recording", async () => {
  const f = fixture();
  f.api.scripting.executeScript = async () => { throw new Error("Cannot access page"); };
  await assert.rejects(f.start(), /Cannot access page/);
  assert.equal(f.data[SESSION_KEY], undefined);
});

test("screenshots require explicit consent, chosen visible tab and window id; count is bounded", async () => {
  const f = fixture();
  await f.start({ screenshots: true });
  for (let i = 0; i < 7; i++) { f.advance(4000); await f.event(); }
  assert.equal(f.calls.captures.length, 5);
  assert.equal(f.calls.captures[0][0], 42);
  assert.equal(f.data[SESSION_KEY].screenshots.length, 5);
  await f.send("extension.stopRecording");
  const upload = JSON.parse(f.calls.fetch[0].init.body);
  assert.equal(upload.screenshotsConsent, true);
  assert.equal(upload.screenshots.length, 5);
});

test("screenshots never capture a different active tab and discard a raced tab switch", async () => {
  const f = fixture();
  await f.start({ screenshots: true });
  f.activeTab.id = 8;
  await f.event();
  assert.equal(f.calls.captures.length, 0);
  f.activeTab.id = 7;
  f.api.tabs.captureVisibleTab = async () => { f.activeTab.id = 8; return "data:image/jpeg;base64,/9j/2Q=="; };
  await f.event();
  assert.equal(f.data[SESSION_KEY].screenshots.length, 0);
});

test("screenshot permission failures warn durably without stopping event capture or losing saved results", async () => {
  const f = fixture();
  await f.start({ screenshots: true });
  f.api.tabs.captureVisibleTab = async () => { throw new Error("activeTab permission required"); };
  const event = await f.event();
  assert.equal(event.ok, true);
  assert.match(event.warning, /toolbar icon/);
  f.restartWorker();
  const recording = await f.send("extension.getStatus");
  assert.equal(recording.status, "recording");
  assert.equal(recording.session.eventCount, 1);
  assert.match(recording.session.warning, /Screenshots unavailable/);
  const stopped = await f.send("extension.stopRecording");
  assert.equal(stopped.status, "ready");
  assert.equal(stopped.result.screenshotCount, 0);
  assert.match(stopped.result.warning, /Screenshots unavailable/);
});

test("failed upload remains durable and retry reuses session identity before generating drafts", async () => {
  const f = fixture({ fetch: async () => ({ ok: false, status: 503 }) });
  await f.start();
  await f.event();
  const first = await f.send("extension.stopRecording");
  assert.equal(first.ok, false);
  assert.equal(first.status, "pending");
  assert.equal(f.data[SESSION_KEY].events.length, 1);
  assert.match(first.error, /HTTP 503/);
  const originalId = JSON.parse(f.calls.fetch[0].init.body).sessionId;
  f.restartWorker();
  f.setFetch(async (url) => ({ ok: true, json: async () => url.endsWith("/record") ? { id: "saved-record" } : { tickets: [{ title: "Draft" }] } }));
  const retry = await f.send("extension.retryUpload");
  assert.equal(retry.status, "ready");
  assert.equal(retry.result.ticketCount, 1);
  assert.equal(f.data[SESSION_KEY], undefined);
  assert.equal(JSON.parse(f.calls.fetch[1].init.body).sessionId, originalId);
  assert.equal(f.calls.fetch[1].init.headers["X-Panther-Client"], "extension-v1");
  assert.deepEqual(JSON.parse(f.calls.fetch[2].init.body), { use_ai: false });
});

test("no recording receipt means capture remains retryable even on HTTP success", async () => {
  const f = fixture({ fetch: async () => ({ ok: true, json: async () => ({}) }) });
  await f.start();
  const result = await f.send("extension.stopRecording");
  assert.equal(result.status, "pending");
  assert.match(result.error, /receipt/);
});

test("generation failure retains saved recording and retries without uploading again", async () => {
  const f = fixture({ fetch: async (url) => url.endsWith("/record") ? { ok: true, json: async () => ({ id: "record-1" }) } : { ok: false, status: 500 } });
  await f.start();
  const result = await f.send("extension.stopRecording");
  assert.equal(result.status, "error");
  assert.equal(result.result.id, "record-1");
  assert.equal(f.data[SESSION_KEY], undefined);
  f.setFetch(async () => ({ ok: true, json: async () => ({ tickets: [] }) }));
  assert.equal((await f.send("extension.retryGeneration")).status, "ready");
  assert.equal(f.calls.fetch.filter((call) => call.url.endsWith("/record")).length, 1);
  await f.send("extension.openReview");
  assert.match(f.calls.tabs[0].url, /options\/options.html\?recording=record-1$/);
});

test("status stays responsive during save and automatic draft generation", async () => {
  let resolveUpload;
  const f = fixture({ fetch: async (url) => url.endsWith("/record") ? new Promise((resolve) => { resolveUpload = resolve; }) : { ok: true, json: async () => ({ tickets: [] }) } });
  await f.start();
  const stopping = f.send("extension.stopRecording");
  while (!resolveUpload) await new Promise((resolve) => setImmediate(resolve));
  assert.equal((await f.send("extension.getStatus")).status, "saving");
  resolveUpload({ ok: true, json: async () => ({ id: "record-1" }) });
  await stopping;
  assert.equal((await f.send("extension.getStatus")).status, "ready");
});

test("same-origin reload reinjects but cross-origin navigation stops and saves", async () => {
  const f = fixture();
  await f.start();
  await f.controller.tabUpdated(7, { status: "complete" }, { url: "https://app.test/other" });
  assert.equal(f.calls.inject.length, 2);
  await f.controller.tabUpdated(7, { url: "https://other.test" }, { url: "https://other.test" });
  assert.equal(f.data[SESSION_KEY], undefined);
  assert.equal(JSON.parse(f.calls.fetch[0].init.body).stopReason, "left_origin");
});

test("closing chosen tab preserves capture; unrelated tab closure is ignored", async () => {
  const f = fixture();
  await f.start();
  await f.controller.tabRemoved(8);
  assert.equal(f.calls.fetch.length, 0);
  await f.controller.tabRemoved(7);
  assert.equal(JSON.parse(f.calls.fetch[0].init.body).stopReason, "tab_closed");
});

test("revoking consent stops capture and rejects subsequent events", async () => {
  const f = fixture();
  await f.start();
  await f.event();
  await f.send("extension.setEnabled", { enabled: false });
  assert.equal(f.data[SETTINGS_KEY].enabled, false);
  assert.equal(JSON.parse(f.calls.fetch[0].init.body).stopReason, "consent_revoked");
  assert.equal((await f.event()).ignored, true);
});

test("browser restart turns unfinished capture into pending without silent upload", async () => {
  const f = fixture();
  await f.start();
  await f.event();
  await f.controller.browserStarted();
  assert.equal(f.data[SESSION_KEY].status, "pending");
  assert.equal(f.data[SESSION_KEY].stopReason, "browser_restarted");
  assert.equal(f.calls.fetch.length, 0);
});

test("browser-session marker prevents restored tab events from resuming capture before onStartup", async () => {
  const f = fixture();
  await f.start();
  await f.event();
  assert.equal(f.sessionData[BROWSER_KEY], true);
  delete f.sessionData[BROWSER_KEY];
  f.restartWorker();
  await f.controller.tabUpdated(7, { status: "complete" }, { url: f.activeTab.url });
  const result = await f.send("extension.getStatus");
  assert.equal(result.status, "pending");
  assert.equal(result.session.stopReason, "browser_or_extension_restarted");
  assert.equal(f.calls.inject.length, 1);
  assert.equal(f.calls.fetch.length, 0);
  assert.equal(f.data[SESSION_KEY].events.length, 1);
});

test("worker restart recovers interrupted upload and generation as retryable", async () => {
  const f = fixture();
  await f.start();
  f.data[SESSION_KEY].status = "saving";
  f.restartWorker();
  assert.equal((await f.send("extension.getStatus")).status, "pending");
  delete f.data[SESSION_KEY];
  f.data[RESULT_KEY] = { id: "record-1", status: "generating" };
  f.restartWorker();
  const result = await f.send("extension.getStatus");
  assert.equal(result.status, "error");
  assert.match(result.result.error, /interrupted/);
});

test("bounded event limit stops and saves instead of growing storage forever", async () => {
  const f = fixture();
  await f.start();
  f.data[SESSION_KEY].events = Array.from({ length: MAX_EVENTS - 1 }, () => ({ type: "click", ts: 1 }));
  await f.event();
  const body = JSON.parse(f.calls.fetch[0].init.body);
  assert.equal(body.events.length, MAX_EVENTS);
  assert.equal(body.stopReason, "event_limit");
});

test("discard requires a stopped session and deletes only its local capture", async () => {
  const f = fixture({ fetch: async () => { throw new Error("offline"); } });
  await f.start();
  await assert.rejects(f.send("extension.discardCapture"), /Stop capture/);
  await f.send("extension.stopRecording");
  await f.send("extension.discardCapture");
  assert.equal(f.data[SESSION_KEY], undefined);
  assert.equal(f.data[SETTINGS_KEY].enabled, true);
});
