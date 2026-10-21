import { BACKEND_ORIGIN } from "../shared/constants.js";
import { loadConsentLog } from "../shared/audit.js";

const elements = Object.fromEntries(["status", "consent", "goal", "screenshots", "automation", "automationAI", "automationTarget", "recordBtn", "stopBtn", "retryBtn", "generateBtn", "refreshAutomationBtn", "retryAutomationBtn", "cancelAutomationBtn", "reviewBtn", "discardBtn", "meta", "warning", "error", "log", "backendBanner"].map((id) => [id, document.getElementById(id)]));
let current = { status: "idle", enabled: false };
let busy = false;
let operationError = "";
let pendingConsent = null;
let automationTarget = "";
let targetError = "";

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
  const job = state.result?.automation;
  const automatic = !!state.result?.automationPolicy;
  const terminal = ["delivered", "blocked", "cancelled"].includes(job?.status);
  const deliveryActive = automatic && (!terminal || !!state.result.error || state.result.cancelRequested === true);
  const sessionAutomatic = !!state.session?.automation && !state.session?.automationWithdrawn;
  const labels = { idle: "Ready to capture", recording: "Capturing", saving: "Saving…", pending: "Upload pending", generating: "Generating drafts…", ready: "Drafts ready", error: "Needs retry" };
  const deliveryLabels = { queued: "Delivery queued", generating: "Generating tickets…", delivering: "Delivering tickets…", retry_wait: "Delivery retry scheduled", delivered: "Tickets delivered", blocked: "Delivery blocked", cancelled: "Delivery cancelled" };
  elements.status.textContent = automatic ? (state.result.error ? "Delivery status needs attention" : deliveryLabels[job?.status] || "Checking delivery…") : labels[state.status] || state.status;
  elements.status.className = "status" + (recording ? " recording" : "");
  elements.consent.checked = pendingConsent === null ? state.enabled : pendingConsent;
  elements.consent.disabled = busy || working;
  elements.goal.disabled = !!state.session || busy || working;
  elements.screenshots.disabled = !!state.session || busy || working;
  if (state.session) elements.screenshots.checked = state.session.screenshotsConsent === true;
  else if (finishedSession) elements.screenshots.checked = false;
  if (state.session) {
    elements.automation.checked = sessionAutomatic;
    elements.automationAI.checked = sessionAutomatic && state.session.automation.use_ai === true;
  } else if (finishedSession) {
    elements.automation.checked = false;
    elements.automationAI.checked = false;
  }
  elements.automation.disabled = !!state.session || busy || working || deliveryActive || !automationTarget;
  elements.automationAI.disabled = !elements.automation.checked || !!state.session || busy || working || deliveryActive;
  const displayedTarget = state.session?.automation?.target_url || (deliveryActive ? state.result.automationPolicy.target_url : automationTarget);
  elements.automationTarget.textContent = displayedTarget ? `Destination: ${displayedTarget}${state.session || deliveryActive ? " · fixed for this session" : ""}` : targetError || "Automatic delivery unavailable: destination is unknown. Manual capture still works.";
  elements.recordBtn.hidden = !!state.session || working || deliveryActive;
  elements.recordBtn.disabled = !state.enabled || busy || working || deliveryActive || (elements.automation.checked && !automationTarget);
  elements.stopBtn.hidden = !recording;
  elements.stopBtn.textContent = sessionAutomatic ? "Stop & deliver tickets" : "Stop & generate drafts";
  elements.stopBtn.disabled = busy;
  elements.retryBtn.hidden = !pending;
  elements.retryBtn.disabled = busy || state.session?.cancelRequested === true || state.session?.cancellationConfirmed === true;
  elements.retryBtn.textContent = sessionAutomatic ? "Retry upload & deliver" : "Retry upload";
  elements.discardBtn.hidden = !pending;
  elements.discardBtn.disabled = busy || (sessionAutomatic && state.session.uploadAttempts > 0);
  elements.generateBtn.hidden = !(state.status === "error" && state.result);
  elements.generateBtn.disabled = busy;
  elements.refreshAutomationBtn.hidden = !automatic && !(sessionAutomatic && state.session.uploadAttempts > 0);
  elements.refreshAutomationBtn.disabled = busy;
  elements.refreshAutomationBtn.textContent = state.session ? "Check upload / delivery" : "Check delivery status";
  elements.retryAutomationBtn.hidden = !automatic || job?.status !== "retry_wait" || !!state.result?.error || state.result?.cancelRequested === true;
  elements.retryAutomationBtn.disabled = busy;
  elements.cancelAutomationBtn.hidden = !sessionAutomatic && !deliveryActive;
  elements.cancelAutomationBtn.disabled = busy || working;
  elements.cancelAutomationBtn.textContent = state.session?.cancelRequested || state.result?.cancelRequested ? "Check cancellation" : "Cancel automatic delivery";
  elements.reviewBtn.hidden = !state.result?.id;
  elements.reviewBtn.disabled = busy;
  elements.meta.textContent = state.session ?
    `${state.session.eventCount} events · ${state.session.screenshotCount} screenshots · started ${new Date(state.session.startTime).toLocaleTimeString()}` :
    automatic ? `${state.result.eventCount} events saved · ${job?.delivered_count || 0}/${job?.ticket_count || 0} verified delivery receipts · ${job?.stage || "status unknown"} · ${state.result.automationPolicy.target_url}. Receipts are historical, not current ticket state. Review-only backlog; no work execution.${state.result.automationCheckedAt ? ` Last status check ${new Date(state.result.automationCheckedAt).toLocaleTimeString()}.` : ""}` :
    state.result ? `${state.result.eventCount} events saved · ${state.result.ticketCount || 0} draft tickets. Review before sending to another system.` :
      "The page panel shows capture status and basic controls.";
  showError(state.session?.error || state.result?.error || job?.error || operationError);
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
elements.automation.addEventListener("change", () => {
  if (!elements.automation.checked) elements.automationAI.checked = false;
  render(current);
});
elements.recordBtn.addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("Choose a webpage to capture.");
    const automation = elements.automation.checked ? { enabled: true, consent: true, target_url: automationTarget, use_ai: elements.automationAI.checked, max_tickets: 5 } : undefined;
    await command("extension.startRecording", { tabId: tab.id, consent: elements.consent.checked, screenshots: elements.screenshots.checked, goal: elements.goal.value, automation });
  } catch (error) { showError(error.message); }
});
elements.stopBtn.addEventListener("click", () => command("extension.stopRecording"));
elements.retryBtn.addEventListener("click", () => command("extension.retryUpload"));
elements.generateBtn.addEventListener("click", () => command("extension.retryGeneration"));
elements.refreshAutomationBtn.addEventListener("click", () => command("extension.refreshAutomation"));
elements.retryAutomationBtn.addEventListener("click", () => command("extension.retryAutomation"));
elements.cancelAutomationBtn.addEventListener("click", () => command("extension.cancelAutomation"));
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
chrome.runtime.sendMessage({ type: "extension.getAutomationTarget" }).then((response) => {
  if (!response?.ok || !response.url) throw new Error(response?.error || "Dashboard destination is unknown.");
  automationTarget = response.url;
  render(current);
}).catch((error) => { targetError = `Automatic delivery unavailable: ${error.message}`; render(current); });
setInterval(refresh, 1200);
