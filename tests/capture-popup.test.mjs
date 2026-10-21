import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const script = (await readFile(new URL("../extension/popup/popup.js", import.meta.url), "utf8")).replace(/^import .*;\n/gm, "");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function fixture(initial, target = "http://127.0.0.1:7878") {
  let state = initial;
  let interval;
  const calls = [];
  const responses = new Map();
  const elements = {};
  const element = () => ({ hidden: false, disabled: false, checked: false, value: "", textContent: "", listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; }, replaceChildren() {} });
  const context = vm.createContext({
    BACKEND_ORIGIN: "http://127.0.0.1:7890", loadConsentLog: async () => [], Date, console, AbortSignal,
    document: { getElementById: (id) => elements[id] ||= element(), createElement: element },
    setInterval: (fn) => { interval = fn; },
    fetch: async () => ({ ok: true }), confirm: () => true,
    chrome: { tabs: { query: async () => [{ id: 7 }] }, runtime: {
      sendMessage: async (message) => {
        calls.push(structuredClone(message));
        if (message.type === "extension.getAutomationTarget") return target ? { ok: true, url: target } : { ok: false, error: "Backend offline; destination unknown." };
        return responses.has(message.type) ? responses.get(message.type)(message) : structuredClone(state);
      }
    } }
  });
  vm.runInContext(script, context);
  return { elements, calls, respond(type, handler) { responses.set(type, handler); }, state(next) { state = next; interval(); }, async click(id) { await elements[id].listeners.click(); await flush(); } };
}

const recording = (screenshotsConsent = true) => ({
  ok: true, enabled: true, status: "recording",
  session: { eventCount: 3, screenshotCount: 1, startTime: 1700000000000, screenshotsConsent }, result: null
});

test("consent checkbox retains the user's selection while its change request is pending", async () => {
  const idle = { ok: true, enabled: false, status: "idle", session: null, result: null };
  const f = fixture(idle);
  await flush();
  let resolveConsent;
  f.respond("extension.setEnabled", () => new Promise((resolve) => { resolveConsent = resolve; }));
  f.elements.consent.checked = true;
  const pending = f.elements.consent.listeners.change();
  assert.equal(f.elements.consent.checked, true);
  assert.equal(f.elements.consent.disabled, true);
  f.state(idle); // An older status poll must not undo the pending selection.
  await flush();
  assert.equal(f.elements.consent.checked, true);
  const enabled = { ...idle, enabled: true };
  f.state(enabled);
  resolveConsent(enabled);
  await pending;
  assert.equal(f.elements.consent.checked, true);
  assert.equal(f.elements.consent.disabled, false);
});

test("failed consent changes restore the confirmed value and show the error", async () => {
  const f = fixture({ ok: true, enabled: true, status: "idle", session: null, result: null });
  await flush();
  let resolveConsent;
  f.respond("extension.setEnabled", () => new Promise((resolve) => { resolveConsent = resolve; }));
  f.elements.consent.checked = false;
  const pending = f.elements.consent.listeners.change();
  assert.equal(f.elements.consent.checked, false);
  resolveConsent({ ok: false, error: "Could not store consent." });
  await pending;
  assert.equal(f.elements.consent.checked, true);
  assert.equal(f.elements.consent.disabled, false);
  assert.equal(f.elements.error.textContent, "Could not store consent.");
});

test("reopened popup shows actual screenshot consent and resets it when the session finishes", async () => {
  const f = fixture(recording(true));
  await flush();
  assert.equal(f.elements.screenshots.checked, true);
  assert.equal(f.elements.screenshots.disabled, true);
  f.state({ ok: true, enabled: true, status: "ready", session: null, result: { id: "rec", eventCount: 3, ticketCount: 1 } });
  await flush();
  assert.equal(f.elements.screenshots.checked, false);
  assert.equal(f.elements.screenshots.disabled, false);
  await f.click("recordBtn");
  assert.equal(f.calls.find((call) => call.type === "extension.startRecording").screenshots, false);
});

test("pending capture exposes retry and discard while preserving its consent display", async () => {
  const state = recording(false); state.status = "pending"; state.session.error = "Backend unavailable; retry.";
  const f = fixture(state);
  await flush();
  assert.equal(f.elements.retryBtn.hidden, false);
  assert.equal(f.elements.discardBtn.hidden, false);
  assert.equal(f.elements.recordBtn.hidden, true);
  assert.equal(f.elements.stopBtn.hidden, true);
  assert.equal(f.elements.screenshots.checked, false);
  assert.equal(f.elements.error.textContent, "Backend unavailable; retry.");
  await f.click("retryBtn");
  assert.ok(f.calls.some((call) => call.type === "extension.retryUpload"));
});

test("popup reports generated draft count and surfaces generation-only retry", async () => {
  const f = fixture({ ok: true, enabled: true, status: "error", session: null, result: { id: "rec", eventCount: 3, ticketCount: 0, error: "Generation failed; recording saved." } });
  await flush();
  assert.equal(f.elements.generateBtn.hidden, false);
  assert.equal(f.elements.retryBtn.hidden, true);
  assert.equal(f.elements.reviewBtn.hidden, false);
  await f.click("generateBtn");
  assert.ok(f.calls.some((call) => call.type === "extension.retryGeneration"));
  f.state({ ok: true, enabled: true, status: "ready", session: null, result: { id: "rec", eventCount: 3, ticketCount: 2 } });
  await flush();
  assert.equal(f.elements.status.textContent, "Drafts ready");
  assert.match(f.elements.meta.textContent, /2 draft tickets/);
});

test("screenshot warnings remain visible independently of a successful save or generation", async () => {
  const state = recording(true); state.session.warning = "Screenshots unavailable; use the toolbar icon.";
  const f = fixture(state);
  await flush();
  assert.equal(f.elements.warning.hidden, false);
  assert.equal(f.elements.error.hidden, true);
  f.state({ ok: true, enabled: true, status: "ready", session: null, result: { id: "rec", eventCount: 3, ticketCount: 1, warning: state.session.warning } });
  await flush();
  assert.equal(f.elements.status.textContent, "Drafts ready");
  assert.equal(f.elements.warning.textContent, state.session.warning);
});

const policy = { enabled: true, consent: true, target_url: "http://127.0.0.1:7878", use_ai: false, max_tickets: 5 };
const automatic = (status = "queued") => ({ ok: true, enabled: true, status: "automation", session: null,
  result: { id: "rec", eventCount: 3, automationPolicy: { ...policy }, automationCheckedAt: 1700000000000,
    automation: { status, stage: "delivery", ticket_count: 2, delivered_count: 1 }, error: "" } });

test("automatic delivery and local AI are unchecked per-session opt-ins with exact visible destination", async () => {
  const f = fixture({ ok: true, enabled: true, status: "idle", session: null, result: null });
  await flush();
  assert.equal(f.elements.automation.checked, false);
  assert.equal(f.elements.automationAI.checked, false);
  assert.equal(f.elements.automationAI.disabled, true);
  assert.match(f.elements.automationTarget.textContent, /http:\/\/127.0.0.1:7878/);
  await f.click("recordBtn");
  assert.equal(f.calls.find((call) => call.type === "extension.startRecording").automation, undefined);
  f.elements.automation.checked = true;
  f.elements.automation.listeners.change();
  assert.equal(f.elements.automationAI.disabled, false);
  assert.equal(f.elements.automationAI.checked, false);
  f.elements.automationAI.checked = true;
  await f.click("recordBtn");
  assert.deepEqual(f.calls.filter((call) => call.type === "extension.startRecording").at(-1).automation, { ...policy, use_ai: true });
});

test("unknown destination disables automatic delivery without disabling manual capture", async () => {
  const f = fixture({ ok: true, enabled: true, status: "idle", session: null, result: null }, null);
  await flush();
  assert.equal(f.elements.automation.disabled, true);
  assert.equal(f.elements.recordBtn.disabled, false);
  assert.match(f.elements.automationTarget.textContent, /destination unknown/);
});

test("previous terminal delivery retains its receipt target while new consent shows and sends the new target", async () => {
  const target = "http://127.0.0.1:9999";
  const f = fixture(automatic("delivered"), target);
  await flush();
  assert.match(f.elements.automationTarget.textContent, /127.0.0.1:9999/);
  assert.doesNotMatch(f.elements.automationTarget.textContent, /fixed for this session/);
  assert.match(f.elements.meta.textContent, /127.0.0.1:7878/);
  assert.equal(f.elements.recordBtn.hidden, false);
  f.elements.automation.checked = true; f.elements.automation.listeners.change();
  await f.click("recordBtn");
  assert.equal(f.calls.find((call) => call.type === "extension.startRecording").automation.target_url, target);
});

test("active session policy stays frozen and Stop advertises delivery; completion resets session opt-ins", async () => {
  const state = recording(); state.session.automation = { ...policy, use_ai: true };
  const f = fixture(state, "http://127.0.0.1:9999"); await flush();
  assert.equal(f.elements.automation.checked, true);
  assert.equal(f.elements.automation.disabled, true);
  assert.equal(f.elements.automationAI.checked, true);
  assert.match(f.elements.automationTarget.textContent, /127.0.0.1:7878.*fixed/);
  assert.equal(f.elements.stopBtn.textContent, "Stop & deliver tickets");
  assert.equal(f.elements.cancelAutomationBtn.hidden, false);
  f.state(automatic()); await flush();
  assert.equal(f.elements.automation.checked, false);
  assert.equal(f.elements.automationAI.checked, false);
  assert.equal(f.elements.recordBtn.hidden, true);
  assert.equal(f.elements.generateBtn.hidden, true);
});

test("backend retry and cancellation controls are separate from upload/generation and disabled while busy", async () => {
  const f = fixture(automatic("retry_wait")); await flush();
  assert.equal(f.elements.retryAutomationBtn.hidden, false);
  assert.equal(f.elements.generateBtn.hidden, true);
  assert.equal(f.elements.retryBtn.hidden, true);
  let resolveCancel;
  f.respond("extension.cancelAutomation", () => new Promise((resolve) => { resolveCancel = resolve; }));
  const pending = f.elements.cancelAutomationBtn.listeners.click();
  assert.equal(f.elements.cancelAutomationBtn.disabled, true);
  f.state(automatic("retry_wait")); await flush();
  assert.equal(f.elements.cancelAutomationBtn.disabled, true);
  resolveCancel(automatic("cancelled")); await pending;
  assert.ok(f.calls.some((call) => call.type === "extension.cancelAutomation"));
});

test("status loss and partial cancellation retain historical receipt counts without claiming fresh ticket verification", async () => {
  const state = automatic("cancelled"); state.result.error = "Cancellation is unconfirmed."; state.result.cancelRequested = true;
  const f = fixture(state); await flush();
  assert.equal(f.elements.status.textContent, "Delivery status needs attention");
  assert.match(f.elements.meta.textContent, /1\/2 verified delivery receipts/);
  assert.match(f.elements.meta.textContent, /historical, not current ticket state/);
  assert.match(f.elements.meta.textContent, /Last status check/);
  assert.equal(f.elements.cancelAutomationBtn.textContent, "Check cancellation");
  assert.equal(f.elements.generateBtn.hidden, true);
  assert.equal(f.elements.recordBtn.hidden, true);
});

test("acknowledged missing-upload cancellation exposes discard but prevents a cancelled upload retry", async () => {
  const state = recording(); state.status = "pending";
  Object.assign(state.session, { automation: { ...policy }, automationWithdrawn: true, cancellationConfirmed: true, uploadAttempts: 1 });
  const f = fixture(state); await flush();
  assert.equal(f.elements.retryBtn.disabled, true);
  assert.equal(f.elements.discardBtn.disabled, false);
  assert.equal(f.elements.cancelAutomationBtn.hidden, true);
});

test("pending automatic upload offers read-only reconciliation separately from retry and cancellation", async () => {
  const state = recording(); state.status = "pending";
  Object.assign(state.session, { automation: { ...policy }, uploadAttempts: 1 });
  const f = fixture(state); await flush();
  assert.equal(f.elements.refreshAutomationBtn.hidden, false);
  assert.equal(f.elements.refreshAutomationBtn.textContent, "Check upload / delivery");
  await f.click("refreshAutomationBtn");
  assert.ok(f.calls.some((call) => call.type === "extension.refreshAutomation"));
  assert.equal(f.calls.some((call) => call.type === "extension.retryUpload"), false);
});
