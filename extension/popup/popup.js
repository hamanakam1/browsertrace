import { DASHBOARD_ORIGIN, RECORD_API } from "../shared/constants.js";
import { loadConsentLog } from "../shared/audit.js";
import { getState, setState } from "../shared/storage.js";

const STATUS = {
  IDLE: "Idle",
  RECORDING: "Recording",
  STOPPING: "Stopping…"
};

const statusEl = document.getElementById("status");
const consentEl = document.getElementById("consent");
const recordBtn = document.getElementById("recordBtn");
const stopBtn = document.getElementById("stopBtn");
const metaEl = document.getElementById("meta");
const logEl = document.getElementById("log");

let enabled = false;
let recording = false;

function setStatus(s) {
  statusEl.textContent = s;
  statusEl.className = "status" + (s === STATUS.RECORDING ? " recording" : "");
  recordBtn.disabled = !enabled || recording;
  stopBtn.disabled = !enabled || !recording;
  stopBtn.style.display = recording ? "inline-block" : "none";
  recordBtn.style.display = recording ? "none" : "inline-block";
}

async function refresh() {
  const state = await getState();
  enabled = !!state.enabled;
  recording = !!state.recording;
  consentEl.checked = enabled;
  setStatus(recording ? STATUS.RECORDING : STATUS.IDLE);
  if (recording) {
    metaEl.textContent = `Session started ${new Date(state.sessionStart || 0).toLocaleTimeString()}`;
  } else {
    metaEl.textContent = "";
  }
  renderLog();
}

async function renderLog() {
  const log = await loadConsentLog();
  logEl.innerHTML = log.slice(-20).reverse().map((e) => {
    const ts = new Date(e.ts).toLocaleTimeString();
    return `<div class="log-entry">[${ts}] ${e.action}${e.tabId !== undefined ? ` tab:${e.tabId}` : ""}</div>`;
  }).join("");
}

consentEl.addEventListener("change", async () => {
  enabled = consentEl.checked;
  await setState({ enabled });
  await chrome.runtime.sendMessage({ type: "extension.setEnabled", enabled });
  setStatus(recording ? STATUS.RECORDING : STATUS.IDLE);
  renderLog();
});

recordBtn.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  try {
    const result = await chrome.runtime.sendMessage({ type: "extension.startRecording", tabId: tab.id });
    recording = true;
    await setState({ recording: true, sessionStart: result.startTime });
    setStatus(STATUS.RECORDING);
  } catch (err) {
    alert("Could not start recording: " + err.message);
  }
});

stopBtn.addEventListener("click", async () => {
  try {
    const result = await chrome.runtime.sendMessage({ type: "extension.stopRecording" });
    recording = false;
    await setState({ recording: false });
    setStatus(STATUS.IDLE);
    metaEl.textContent = `Saved ${result.eventCount} events`;
  } catch (err) {
    alert("Could not stop recording: " + err.message);
  }
});

// Listen for state changes from background (e.g. recording indicator).
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "extension.recordingState") {
    recording = msg.recording;
    setStatus(recording ? STATUS.RECORDING : STATUS.IDLE);
    renderLog();
  }
});

refresh();
