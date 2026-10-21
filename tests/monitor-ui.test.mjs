import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const model = (await readFile(new URL("../extension/monitor/status-model.js", import.meta.url), "utf8")).replace(/^export /gm, "");
const script = (await readFile(new URL("../extension/monitor/monitor.js", import.meta.url), "utf8")).replace(/^import .*;\n/gm, "");
const html = await readFile(new URL("../extension/monitor/monitor.html", import.meta.url), "utf8");
const css = await readFile(new URL("../extension/monitor/monitor.css", import.meta.url), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject}; };
const idle = power => ({ok: true, enabled: false, status: "idle", power: {state: power}, session: null, result: null});
const capture = () => ({...idle("on"), enabled: true, status: "recording", session: {eventCount: 4, screenshotCount: 1, automation: {enabled: true}}});
const delivery = () => ({...idle("on"), status: "automation", result: {id: "result", eventCount: 4, automationPolicy: {enabled: true}, automationCheckedAt: 1700000000000, automation: {status: "delivering", delivered_count: 1, ticket_count: 2, stage: "delivery"}}});

function fixture(initial, {initialStatus, confirmation = true} = {}) {
  let state = initial, now = 1700000001000, nextTimer = 0;
  const calls = [], elements = {}, responses = new Map(), timers = new Map(), listeners = {};
  if (initialStatus) responses.set("extension.getStatus", initialStatus);
  class TestDate extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
  const element = () => ({hidden: false, disabled: false, textContent: "", dataset: {}, listeners: {}, addEventListener(type, fn) {this.listeners[type] = fn;}, set innerHTML(value) {throw new Error(`Unsafe HTML write: ${value}`);}});
  const context = vm.createContext({
    Date: TestDate, console,
    document: {getElementById(id) {return elements[id] ||= element();}},
    window: {addEventListener(type, fn) {listeners[type] = fn;}},
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, {fn, at: now + delay}); return id; },
    clearTimeout(id) {timers.delete(id);},
    setInterval(fn, delay) {const id = ++nextTimer; timers.set(id, {fn, at: now + delay, interval: delay}); return id;},
    clearInterval(id) {timers.delete(id);},
    confirm: () => confirmation,
    chrome: {runtime: {sendMessage: async message => {
      calls.push(structuredClone(message));
      if (responses.has(message.type)) return responses.get(message.type)(message);
      if (message.type === "extension.turnOn" || message.type === "extension.turnOff") {
        state = {...state, enabled: false, power: {state: message.type === "extension.turnOn" ? "on" : "off"}};
        return {ok: true, power: structuredClone(state.power)};
      }
      return structuredClone(state);
    }}}
  });
  vm.runInContext(`${model}\n${script}`, context);
  return {elements, calls, timers,
    setState(value) {state = value;},
    respond(type, handler) {responses.set(type, handler);},
    clearResponse(type) {responses.delete(type);},
    close() {listeners.pagehide();},
    async click(id) {if (!elements[id].disabled && !elements[id].hidden) {const pending = elements[id].listeners.click(); await flush(); return pending;}},
    async advance(ms) {
      const target = now + ms;
      while (true) {
        const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        const [id, timer] = next; now = timer.at;
        if (timer.interval) timer.at += timer.interval; else timers.delete(id);
        timer.fn(); await flush();
      }
      now = target; await flush();
    }
  };
}

test("opening and polling monitor has no capture, delivery, backend fetch or native-start side effect", async () => {
  const f = fixture(idle("off")); await flush(); await f.advance(6000);
  assert.equal(f.elements.powerStatus.textContent, "Panther is off");
  assert.ok(f.calls.length >= 3);
  assert.ok(f.calls.every(call => call.type === "extension.getStatus"));
  assert.equal(f.elements.turnOnBtn.hidden, false);
  assert.equal(f.elements.turnOffBtn.disabled, true);
  assert.doesNotMatch(script, /\bfetch\s*\(|connectNative|startRecording|setEnabled|retryAutomation|retryUpload/);
});

test("explicit On requires no capture consent in this window and does not start recording", async () => {
  const f = fixture(idle("off")); await flush(); await f.click("turnOnBtn");
  assert.equal(f.elements.powerStatus.textContent, "Panther is on");
  assert.equal(f.elements.activity.textContent, "Idle");
  assert.equal(f.elements.turnOffBtn.disabled, false);
  assert.ok(f.calls.some(call => call.type === "extension.turnOn"));
  assert.ok(f.calls.every(call => ["extension.getStatus", "extension.turnOn"].includes(call.type)));
});

test("hanging status has one underlying request, times out unknown and cannot revive cached On", async () => {
  const f = fixture(idle("on")); await flush();
  const pending = deferred(); f.respond("extension.getStatus", () => pending.promise);
  await f.advance(8000);
  assert.equal(f.elements.powerStatus.textContent, "Status unknown");
  assert.equal(f.elements.activity.textContent, "Unknown");
  assert.equal(f.elements.turnOffBtn.disabled, false);
  assert.match(f.elements.error.textContent, /timed out/);
  assert.equal(f.calls.filter(call => call.type === "extension.getStatus").length, 2);
  await f.advance(10000);
  assert.equal(f.calls.filter(call => call.type === "extension.getStatus").length, 2);
  pending.resolve(idle("on")); await flush();
  assert.equal(f.elements.powerStatus.textContent, "Status unknown");
  f.clearResponse("extension.getStatus"); await f.advance(2000);
  assert.equal(f.elements.powerStatus.textContent, "Panther is on");
});

test("Off bypasses a stuck status request and ignores its late On response", async () => {
  const pending = deferred(); const f = fixture(capture(), {initialStatus: () => pending.promise}); await flush();
  assert.equal(f.elements.turnOffBtn.disabled, false);
  await f.click("turnOffBtn");
  assert.equal(f.elements.powerStatus.textContent, "Panther is off");
  pending.resolve(capture()); await flush();
  assert.equal(f.elements.powerStatus.textContent, "Panther is off");
  assert.equal(f.elements.activity.textContent, "Idle");
});

test("Off can interrupt pending On and the late On response cannot overwrite it", async () => {
  const f = fixture(idle("off")); await flush();
  const pending = deferred(); f.respond("extension.turnOn", () => pending.promise);
  const onRequest = f.click("turnOnBtn"); await flush();
  assert.equal(f.elements.powerStatus.textContent, "Turning on…");
  assert.equal(f.elements.turnOffBtn.disabled, false);
  await f.click("turnOffBtn");
  pending.resolve({ok: true, power: {state: "on"}}); await onRequest;
  assert.equal(f.elements.powerStatus.textContent, "Panther is off");
});

test("Off then On during a hung status poll keeps old work retained and idle", async () => {
  const f = fixture(capture()); await flush();
  const pending = deferred(); f.respond("extension.getStatus", () => pending.promise);
  await f.advance(2000);
  await f.click("turnOffBtn");
  await f.click("turnOnBtn");
  assert.equal(f.elements.powerStatus.textContent, "Panther is on");
  assert.equal(f.elements.activity.textContent, "Idle");
  assert.match(f.elements.activityDetail.textContent, /retained, not running/);
  assert.equal(f.elements.stopBtn.hidden, true);
  assert.equal(f.elements.cancelBtn.hidden, true);
  assert.equal(f.elements.eventCount.textContent, "4");
  pending.resolve(capture()); await flush();
  assert.equal(f.elements.activity.textContent, "Idle");
  assert.equal(f.elements.stopBtn.hidden, true);
});

for (const command of ["extension.stopRecording", "extension.cancelAutomation"]) {
  test(`Off remains reachable during pending ${command} and fences its completion`, async () => {
    const f = fixture(capture()); await flush();
    const pending = deferred(); f.respond(command, () => pending.promise);
    const operation = f.click(command === "extension.stopRecording" ? "stopBtn" : "cancelBtn"); await flush();
    assert.equal(f.elements.turnOffBtn.disabled, false);
    await f.click("turnOffBtn");
    assert.equal(f.elements.activity.textContent, "Idle");
    pending.resolve({...delivery(), status: "generating"}); await operation;
    assert.equal(f.elements.powerStatus.textContent, "Panther is off");
    assert.equal(f.elements.activity.textContent, "Idle");
    assert.equal(f.elements.operationStatus.hidden, true);
    assert.equal(f.elements.eventCount.textContent, "4");
    assert.equal(f.elements.historyNote.hidden, false);
  });
}

test("Off remains reachable during reported generation and delivery with historical counts", async () => {
  for (const input of [{...delivery(), status: "generating", result: {...delivery().result, automationPolicy: null}}, delivery()]) {
    const f = fixture(input); await flush();
    assert.equal(f.elements.turnOffBtn.disabled, false);
    await f.click("turnOffBtn");
    assert.equal(f.elements.activity.textContent, "Idle");
    assert.equal(f.elements.eventCount.textContent, "4");
    assert.equal(f.elements.reviewBtn.disabled, true);
    assert.equal(f.elements.cancelBtn.hidden, true);
  }
});

test("cancel requires confirmation and only invokes the scoped cancel command", async () => {
  const denied = fixture(delivery(), {confirmation: false}); await flush(); await denied.click("cancelBtn");
  assert.ok(denied.calls.every(call => call.type === "extension.getStatus"));
  const accepted = fixture(delivery()); await flush(); await accepted.click("cancelBtn");
  assert.ok(accepted.calls.some(call => call.type === "extension.cancelAutomation"));
});

test("old results remain explicitly historical while a new capture advances", async () => {
  const f = fixture({...capture(), result: delivery().result}); await flush();
  assert.equal(f.elements.activity.textContent, "Capturing");
  assert.match(f.elements.draftSummary.textContent, /saved result/);
  assert.match(f.elements.receiptSummary.textContent, /Historical receipts, not current ticket state/);
  assert.match(f.elements.receiptTime.textContent, /does not perform destination readback/);
  f.setState({...capture(), session: {...capture().session, eventCount: 7}, result: delivery().result}); await f.advance(2000);
  assert.equal(f.elements.eventCount.textContent, "7");
});

test("draft and proposal counts use singular and plural labels", async () => {
  const f = fixture({...idle("on"), status: "ready", result: {id: "result", ticketCount: 1}}); await flush();
  assert.equal(f.elements.draftSummary.textContent, "1 draft ticket");
  f.setState(delivery()); await f.advance(2000);
  assert.equal(f.elements.draftSummary.textContent, "2 ticket proposals");
});

test("errors and stage text are text-only, and private captured fields are not rendered", async () => {
  const unsafe = '<img src=x onerror="alert(1)">';
  const input = delivery(); input.result.error = unsafe; input.result.automation.stage = unsafe;
  input.result.goal = "PRIVATE GOAL"; input.result.events = ["PRIVATE EVENT"];
  const f = fixture(input); await flush();
  assert.equal(f.elements.error.textContent, unsafe);
  assert.match(f.elements.receiptSummary.textContent, /<img/);
  assert.doesNotMatch(Object.values(f.elements).map(element => element.textContent).join(" "), /PRIVATE/);
  assert.doesNotMatch(script, /innerHTML|insertAdjacentHTML|document\.write/);
});

test("failed confirmation disables session actions but not Off", async () => {
  const f = fixture(capture()); await flush();
  f.respond("extension.getStatus", () => {throw new Error("worker disconnected");}); await f.advance(2000);
  assert.equal(f.elements.powerStatus.textContent, "Status unknown");
  assert.equal(f.elements.stopBtn.hidden, true);
  assert.equal(f.elements.cancelBtn.hidden, true);
  assert.equal(f.elements.turnOffBtn.disabled, false);
  assert.match(f.elements.error.textContent, /worker disconnected/);
});

test("closing monitor only clears timers and ignores late responses, never stops runtime", async () => {
  const pending = deferred(); const f = fixture(idle("on"), {initialStatus: () => pending.promise}); await flush();
  f.close(); pending.resolve(capture()); await flush(); await f.advance(10000);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].type, "extension.getStatus");
  assert.equal(f.elements.activity.textContent, "Unknown");
});

test("markup keeps power controls sticky, named and keyboard reachable at narrow widths", () => {
  for (const id of ["turnOnBtn", "turnOffBtn", "stopBtn", "cancelBtn", "reviewBtn"]) assert.match(html, new RegExp(`<button id="${id}"`));
  assert.match(html, /aria-label="Panther runtime controls"/);
  assert.match(html, /Closing this window does not turn Panther off/);
  assert.match(html, /not an always-on-top overlay/);
  assert.match(css, /\.power-panel\s*\{[^}]*position:\s*sticky/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /@media \(max-width: 340px\)/);
});
