import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const script = await readFile(new URL("../extension/content/content.js", import.meta.url), "utf8");

class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.listeners = {}; this.style = {}; this.textContent = ""; }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key] ?? null; }
  append(...children) { for (const child of children) { this.children.push(child); child.parent = this; } }
  appendChild(child) { this.append(child); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
  attachShadow(options) { this.shadowOptions = options; this.shadow = new Element("shadow"); return this.shadow; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  closest() { return this.semantic || this; }
  querySelector() { return this.editableChild || null; }
}

function fixture() {
  const messages = [], listeners = {}, windowListeners = {}, timers = new Map();
  let nextTimer = 0, onMessage;
  let status = { ok: true, status: "recording", session: { eventCount: 0 } };
  const root = new Element("html");
  const context = vm.createContext({
    URL, Date, console,
    location: { href: "http://127.0.0.1:8080/#master" },
    document: { documentElement: root, createElement: (tag) => new Element(tag), addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); } },
    window: { addEventListener: (type, fn) => { windowListeners[type] = fn; } },
    setInterval: (fn, interval) => { timers.set(++nextTimer, { fn, interval }); return nextTimer; },
    clearInterval: (id) => timers.delete(id),
    chrome: { runtime: {
      onMessage: { addListener: (fn) => { onMessage = fn; } },
      sendMessage: async (message) => {
        messages.push(structuredClone(message));
        if (message.type === "page.getStatus" || message.type === "page.stop") return status;
        return { ok: true, eventCount: messages.filter((item) => item.type === "page.interaction").length };
      }
    } }
  });
  vm.runInContext(script, context);
  return {
    context, root, messages, listeners, windowListeners, timers,
    start(automationEnabled = false) { onMessage({ type: "extension.captureStart", sessionId: "session-1", automationEnabled }, {}, () => {}); },
    stop() { onMessage({ type: "extension.captureStop" }, {}, () => {}); },
    state(value) { status = value; },
    dispatch(type, target, path = [target], isTrusted = true) {
      for (const listener of listeners[type] || []) listener({ target, isTrusted, composedPath: () => path });
    },
    tick(interval) { for (const timer of timers.values()) if (timer.interval === interval) timer.fn(); },
    events() { return messages.filter((message) => message.type === "page.interaction"); }
  };
}

test("content installs once, stays idle until Start, and renders isolated stop/review controls", () => {
  const f = fixture();
  f.dispatch("click", new Element("button"));
  assert.equal(f.events().length, 0);
  vm.runInContext(script, f.context);
  assert.equal(f.listeners.click.length, 1);
  f.start();
  assert.equal(f.events()[0].payload.type, "navigation");
  const panel = f.root.children[0];
  assert.equal(panel.id, "panther-intent-capture-controls");
  assert.equal(panel.shadowOptions.mode, "closed");
  const box = panel.shadow.children[1];
  assert.equal(box.children[0].textContent, "Panther · Capturing");
  assert.equal(box.children[2].children[0].textContent, "Stop & generate");
});

test("automatic capture panel labels Stop honestly and reports historical delivery receipts without execution claims", async () => {
  const f = fixture(); f.start(true);
  const box = f.root.children[0].shadow.children[1];
  assert.equal(box.children[2].children[0].textContent, "Stop & deliver");
  f.state({ ok: true, status: "automation", session: null, result: { id: "rec", automationPolicy: {}, automation: { status: "cancelled", ticket_count: 2, delivered_count: 1 } } });
  f.tick(1000); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(box.children[0].textContent, "Panther · Delivery cancelled");
  assert.match(box.children[1].textContent, /1\/2 historical delivery receipts.*no work execution/);
  assert.equal(box.children[2].children[0].hidden, true);
});

test("nested click targets resolve to dashboard semantic ancestors without recording panel clicks", () => {
  const f = fixture(); f.start();
  const button = new Element("button");
  button.setAttribute("data-tab", "master");
  button.setAttribute("data-ticket", "M-12345");
  button.textContent = "Open";
  const icon = new Element("span"); icon.semantic = button;
  f.dispatch("click", icon);
  const event = f.events().at(-1).payload;
  assert.equal(event.target.tag, "button");
  assert.equal(event.target.dataTab, "master");
  assert.equal(event.target.dataTicket, "M-12345");
  const count = f.events().length;
  f.dispatch("click", new Element("button"), [f.root.children[0]]);
  f.dispatch("click", button, [button], false);
  assert.equal(f.events().length, count);
});

test("change capture never reads input values or typed keys and ignores editable labels", () => {
  const f = fixture(); f.start();
  const input = new Element("input");
  input.setAttribute("type", "password");
  Object.defineProperty(input, "value", { get() { throw new Error("Input value was read"); } });
  f.dispatch("change", input);
  assert.equal(f.events().at(-1).payload.target.label, "");
  assert.equal(f.events().at(-1).payload.target.type, "password");
  const buttonWithEditor = new Element("button");
  buttonWithEditor.editableChild = new Element("textarea");
  buttonWithEditor.textContent = "nested private typed label";
  f.dispatch("click", buttonWithEditor);
  assert.equal(f.events().at(-1).payload.target.label, "");
  assert.equal(f.listeners.keydown, undefined);
  const editableButton = new Element("button");
  editableButton.isContentEditable = true;
  editableButton.textContent = "private typed label";
  f.dispatch("click", editableButton);
  assert.equal(f.events().at(-1).payload.target.label, "");
});

test("hash and pushState-style route changes are captured once with private query removed", () => {
  const f = fixture(); f.start();
  f.context.location.href = "http://127.0.0.1:8080/?token=secret#work-board";
  f.windowListeners.hashchange();
  assert.equal(f.events().at(-1).payload.url, "http://127.0.0.1:8080/#work-board");
  const count = f.events().length;
  f.tick(500);
  assert.equal(f.events().length, count);
  f.context.location.href = "http://127.0.0.1:8080/settings?password=secret";
  f.tick(500);
  assert.equal(f.events().at(-1).payload.url, "http://127.0.0.1:8080/settings");
});

test("Stop disables event capture and Hide removes only controls without stopping capture", () => {
  const f = fixture(); f.start();
  const box = f.root.children[0].shadow.children[1];
  box.children[2].children[2].listeners.click();
  assert.equal(f.root.children.length, 0);
  f.dispatch("click", new Element("button"));
  assert.equal(f.events().length, 2);
  f.stop();
  f.dispatch("click", new Element("button"));
  assert.equal(f.events().length, 2);
  assert.equal([...f.timers.values()].filter((timer) => timer.interval === 500).length, 0);
});

test("floating panel exposes generating and ready draft state and links to review", async () => {
  const f = fixture(); f.start();
  const box = f.root.children[0].shadow.children[1];
  f.state({ ok: true, status: "generating", result: { id: "rec", eventCount: 3 } });
  f.tick(1000); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(box.children[0].textContent, "Panther · Generating drafts…");
  f.state({ ok: true, status: "ready", result: { id: "rec", eventCount: 3, ticketCount: 2 } });
  f.tick(1000); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(box.children[0].textContent, "Panther · Drafts ready");
  assert.equal(box.children[2].children[1].hidden, false);
  box.children[2].children[1].listeners.click();
  assert.equal(f.messages.at(-1).type, "page.openReview");
});
