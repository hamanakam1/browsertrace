import { monitorView } from "./status-model.js";

const elements = Object.fromEntries(["powerStatus", "powerDot", "turnOnBtn", "turnOffBtn", "runtimeHelp", "activity", "activityDetail", "operationStatus", "connectionStatus", "error", "warning", "countsHeading", "eventCount", "screenshotCount", "historyNote", "resultSummary", "draftSummary", "receiptSummary", "receiptTime", "stopBtn", "cancelBtn", "reviewBtn"].map(id => [id, document.getElementById(id)]));
const POLL_MS = 1800;
const STATUS_TIMEOUT_MS = 6000;
let snapshot = {};
let checkedAt = null;
let connected = false;
let connectionError = "";
let operationError = "";
let pendingPower = null;
let operationPending = "";
let epoch = 0;
let operationEpoch = 0;
let polling = false;
let closed = false;

const clockText = timestamp => new Date(timestamp).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
const view = () => monitorView(snapshot, {checkedAt, now: Date.now(), connected, pendingPower, connectionError, operationError});

function render() {
  const state = view();
  elements.powerStatus.textContent = state.title;
  elements.powerDot.dataset.state = state.power;
  elements.runtimeHelp.textContent = state.runtimeHelp;
  elements.turnOnBtn.hidden = !state.showOn;
  elements.turnOnBtn.disabled = !state.showOn;
  // This lane is never disabled by a pending Stop, cancel, review or status poll.
  elements.turnOffBtn.disabled = state.offDisabled;
  elements.activity.textContent = state.activity;
  elements.activity.dataset.tone = state.tone;
  elements.activityDetail.textContent = state.detail;
  elements.operationStatus.textContent = operationPending;
  elements.operationStatus.hidden = !operationPending;
  const age = checkedAt ? Math.max(0, Math.floor((Date.now() - checkedAt) / 1000)) : 0;
  elements.connectionStatus.textContent = checkedAt ? `${state.fresh ? "Status confirmed" : "Status stale · last confirmation"} ${clockText(checkedAt)} · ${age}s ago` : "No confirmed status yet.";
  elements.connectionStatus.dataset.stale = String(!state.fresh);
  elements.error.textContent = state.error;
  elements.error.hidden = !state.error;
  elements.warning.textContent = state.warning;
  elements.warning.hidden = !state.warning;
  elements.countsHeading.textContent = state.countsHeading;
  elements.eventCount.textContent = String(state.events);
  elements.screenshotCount.textContent = String(state.screenshots);
  elements.historyNote.hidden = !(state.retained && state.historical);
  elements.resultSummary.hidden = !state.hasResult;
  const draftLabel = state.hasReceipts ? (state.drafts === 1 ? "ticket proposal" : "ticket proposals") : (state.drafts === 1 ? "draft ticket" : "draft tickets");
  elements.draftSummary.textContent = `${state.drafts} ${draftLabel}${state.resultHistorical ? " · saved result" : ""}`;
  elements.receiptSummary.hidden = !state.hasReceipts;
  elements.receiptSummary.textContent = `${state.delivered}/${state.ticketTotal} verified delivery receipts${state.stage ? ` · recorded stage: ${state.stage}` : ""}. Historical receipts, not current ticket state.`;
  elements.receiptTime.hidden = !state.hasReceipts;
  elements.receiptTime.textContent = state.receiptCheckedAt ? `Last delivery check: ${new Date(state.receiptCheckedAt).toLocaleString()}. This window does not perform destination readback.` : "No confirmed delivery check time. This window does not perform destination readback.";
  elements.stopBtn.hidden = !state.canStop;
  elements.stopBtn.disabled = !state.canStop || !!operationPending;
  elements.stopBtn.textContent = state.stopLabel;
  elements.cancelBtn.hidden = !state.canCancel;
  elements.cancelBtn.disabled = !state.canCancel || !!operationPending;
  elements.cancelBtn.textContent = state.cancelLabel;
  elements.reviewBtn.hidden = !state.hasResult;
  elements.reviewBtn.disabled = !state.canReview || !!operationPending;
}

function timedMessage(type, timeoutMs) {
  let timer;
  const operation = Promise.resolve().then(() => chrome.runtime.sendMessage({type}));
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("The extension response timed out. Completion is unconfirmed; use Turn off if needed.")), timeoutMs);
  });
  return {operation, response: Promise.race([operation, timeout]).finally(() => clearTimeout(timer))};
}

async function poll() {
  if (closed || polling || pendingPower) return;
  polling = true;
  const requestEpoch = epoch;
  const request = timedMessage("extension.getStatus", STATUS_TIMEOUT_MS);
  // Keep one underlying getStatus request in flight even after a timeout.
  // Chromium messaging cannot be aborted. A late reply only releases the lock;
  // it never retroactively becomes a fresh confirmation.
  request.operation.then(() => { polling = false; }, () => { polling = false; });
  try {
    const response = await request.response;
    if (closed || requestEpoch !== epoch || pendingPower) return;
    if (!response?.ok || !response.power) throw new Error(response?.error || "The extension did not return a confirmed runtime state.");
    snapshot = response;
    checkedAt = Date.now();
    connected = true;
    connectionError = "";
  } catch (error) {
    if (closed || requestEpoch !== epoch || pendingPower) return;
    connected = false;
    connectionError = `${error.message} If status does not recover, close and reopen this window.`;
  }
  render();
}

async function changePower(on) {
  const state = view();
  if (closed || (on ? !state.showOn : state.offDisabled)) return;
  const requestEpoch = ++epoch;
  ++operationEpoch;
  operationPending = "";
  operationError = "";
  connectionError = "";
  pendingPower = on ? "starting" : "stopping";
  snapshot = {...snapshot, enabled: false};
  // Preserve the last evidence, but never replay old work as active after a
  // power transition. Only a fresh getStatus can establish current activity.
  if (snapshot.session) snapshot.session = {...snapshot.session, powerPaused: true};
  if (snapshot.result) snapshot.result = {...snapshot.result, powerPaused: true};
  render();
  try {
    const response = await timedMessage(on ? "extension.turnOn" : "extension.turnOff", on ? 20000 : 35000).response;
    if (closed || requestEpoch !== epoch) return;
    if (!response?.power) throw new Error(response?.error || "The runtime did not confirm its state.");
    snapshot = {...snapshot, enabled: false, power: response.power};
    checkedAt = Date.now();
    connected = true;
    if (!response.ok && !response.power.error) operationError = response.error || "The power change was not confirmed.";
  } catch (error) {
    if (closed || requestEpoch !== epoch) return;
    connected = false;
    connectionError = error.message;
  } finally {
    if (!closed && requestEpoch === epoch) {
      pendingPower = null;
      render();
      poll();
    }
  }
}

async function command(type) {
  const state = view();
  const allowed = {"extension.stopRecording": state.canStop, "extension.cancelAutomation": state.canCancel, "extension.openReview": state.canReview};
  if (closed || operationPending || !allowed[type]) return;
  if (type === "extension.cancelAutomation" && !confirm("Cancel remaining automatic delivery? Already delivered tickets and requests in flight cannot be undone.")) return;
  const requestEpoch = epoch, requestOperation = ++operationEpoch;
  operationError = "";
  operationPending = {"extension.stopRecording": "Stop requested. Waiting for the capture result…", "extension.cancelAutomation": "Cancellation requested. Waiting for confirmation…", "extension.openReview": "Opening the saved result…"}[type];
  render();
  try {
    const response = await timedMessage(type, 30000).response;
    if (closed || requestEpoch !== epoch || requestOperation !== operationEpoch) return;
    if (!response?.ok) throw new Error(response?.error || "The operation was not confirmed.");
    // Power is confirmed only by the dedicated status or power lane. In
    // particular, a completed Stop cannot replay an old On state after Off.
    if (response.status) snapshot = {...response, power: snapshot.power};
  } catch (error) {
    if (closed || requestEpoch !== epoch || requestOperation !== operationEpoch) return;
    operationError = error.message;
  } finally {
    if (!closed && requestEpoch === epoch && requestOperation === operationEpoch) {
      operationPending = "";
      render();
      poll();
    }
  }
}

elements.turnOnBtn.addEventListener("click", () => changePower(true));
elements.turnOffBtn.addEventListener("click", () => changePower(false));
elements.stopBtn.addEventListener("click", () => command("extension.stopRecording"));
elements.cancelBtn.addEventListener("click", () => command("extension.cancelAutomation"));
elements.reviewBtn.addEventListener("click", () => command("extension.openReview"));
const pollTimer = setInterval(poll, POLL_MS);
const freshnessTimer = setInterval(render, 1000);
window.addEventListener("pagehide", () => {
  closed = true;
  ++epoch;
  clearInterval(pollTimer);
  clearInterval(freshnessTimer);
});
render();
poll();
