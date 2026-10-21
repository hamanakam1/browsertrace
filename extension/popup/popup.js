import { BACKEND_ORIGIN } from "../shared/constants.js";
import { loadConsentLog } from "../shared/audit.js";

const elements = Object.fromEntries(["status", "consent", "goal", "screenshots", "recordBtn", "stopBtn", "retryBtn", "generateBtn", "reviewBtn", "discardBtn", "meta", "warning", "error", "log", "backendBanner"].map((id) => [id, document.getElementById(id)]));
let current = { status: "idle", enabled: false };
let busy = false;
let operationError = "";
let pendingConsent = null;

function showError(message = "") {
  elements.error.textContent = message;
  elements.error.hidden = !message;
}

function render(state) {
  const finishedSession = current.session && !state.session;
  current = state;
  const recording = state.status === "recording";
  const pending = state.status === "pending";
  const working = ["saving", "generating"].includes(state.status);
  const labels = { idle: "Ready to capture", recording: "Capturing", saving: "Saving…", pending: "Upload pending", generating: "Generating drafts…", ready: "Drafts ready", error: "Needs retry" };
  elements.status.textContent = labels[state.status] || state.status;
  elements.status.className = "status" + (recording ? " recording" : "");
  elements.consent.checked = pendingConsent === null ? state.enabled : pendingConsent;
  elements.consent.disabled = busy || working;
  elements.goal.disabled = !!state.session || busy || working;
  elements.screenshots.disabled = !!state.session || busy || working;
  if (state.session) elements.screenshots.checked = state.session.screenshotsConsent === true;
  else if (finishedSession) elements.screenshots.checked = false;
  elements.recordBtn.hidden = !!state.session || working;
  elements.recordBtn.disabled = !state.enabled || busy || working;
  elements.stopBtn.hidden = !recording;
  elements.stopBtn.disabled = busy;
  elements.retryBtn.hidden = !pending;
  elements.retryBtn.disabled = busy;
  elements.discardBtn.hidden = !pending;
  elements.discardBtn.disabled = busy;
  elements.generateBtn.hidden = !(state.status === "error" && state.result);
  elements.generateBtn.disabled = busy;
  elements.reviewBtn.hidden = !state.result?.id;
  elements.reviewBtn.disabled = busy;
  elements.meta.textContent = state.session ?
    `${state.session.eventCount} events · ${state.session.screenshotCount} screenshots · started ${new Date(state.session.startTime).toLocaleTimeString()}` :
    state.result ? `${state.result.eventCount} events saved · ${state.result.ticketCount || 0} draft tickets. Review before sending to another system.` :
      "The page panel shows capture status and basic controls.";
  showError(state.session?.error || state.result?.error || operationError);
  elements.warning.textContent = state.session?.warning || state.result?.warning || "";
  elements.warning.hidden = !elements.warning.textContent;
}

async function renderLog() {
  const log = await loadConsentLog();
  elements.log.replaceChildren(...log.slice(-8).reverse().map((entry) => {
    const row = document.createElement("div");
    row.className = "log-entry";
    row.textContent = `${new Date(entry.ts).toLocaleTimeString()} · ${entry.action.replaceAll("_", " ")}`;
    return row;
  }));
}

async function refresh() {
  try {
    const state = await chrome.runtime.sendMessage({ type: "extension.getStatus" });
    if (!state?.ok) throw new Error(state?.error || "Capture worker did not respond.");
    render(state);
    await renderLog();
  } catch (error) { showError(error.message); }
}

async function command(type, fields = {}) {
  operationError = "";
  const changesConsent = type === "extension.setEnabled";
  if (changesConsent) pendingConsent = fields.enabled === true;
  busy = true;
  render(current);
  try {
    const response = await chrome.runtime.sendMessage({ type, ...fields });
    if (response?.status) render(response);
    if (!response?.ok) throw new Error(response?.error || "The operation did not complete.");
    await refresh();
  } catch (error) { operationError = error.message; }
  finally {
    if (changesConsent) pendingConsent = null;
    busy = false;
    render(current);
  }
}

elements.consent.addEventListener("change", () => command("extension.setEnabled", { enabled: elements.consent.checked }));
elements.recordBtn.addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("Choose a webpage to capture.");
    await command("extension.startRecording", { tabId: tab.id, consent: elements.consent.checked, screenshots: elements.screenshots.checked, goal: elements.goal.value });
  } catch (error) { showError(error.message); }
});
elements.stopBtn.addEventListener("click", () => command("extension.stopRecording"));
elements.retryBtn.addEventListener("click", () => command("extension.retryUpload"));
elements.generateBtn.addEventListener("click", () => command("extension.retryGeneration"));
elements.reviewBtn.addEventListener("click", () => command("extension.openReview"));
elements.discardBtn.addEventListener("click", () => {
  if (confirm("Discard this capture from the browser? Its events cannot be recovered.")) command("extension.discardCapture");
});

async function checkBackend() {
  try {
    const response = await fetch(`${BACKEND_ORIGIN}/api/extension/health`, { signal: AbortSignal.timeout(3000) });
    elements.backendBanner.hidden = response.ok;
  } catch { elements.backendBanner.hidden = false; }
}
refresh();
checkBackend();
setInterval(refresh, 1200);
