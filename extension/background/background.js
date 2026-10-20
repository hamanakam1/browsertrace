import { DASHBOARD_ORIGIN } from "./shared/constants.js";
import { recordConsentEvent } from "./shared/audit.js";
import { sanitizeEventPayload } from "./shared/redaction.js";

const EXTENSION_ID = chrome.runtime.id;
let recordingSession = null; // { tabId, startTime, chunks, events, port }

const PORT = 7878;
const RECORD_API = `${DASHBOARD_ORIGIN}/api/extension/record`;
const CONSENT_LOG_KEY = "extension.consent.log";

/** Ensure an offscreen document exists so MediaRecorder survives SW suspension. */
async function ensureOffscreen() {
  const existing = (await chrome.offscreen.getAllDocuments()).filter(
    (d) => d.url.includes("offscreen.html")
  );
  if (existing.length > 0) return existing[0];
  return await chrome.offscreen.createDocument({
    url: "offscreen/offscreen.html",
    reasons: ["MEDIA_RECORDING"],
    justification: "Keep MediaRecorder alive while recording a dashboard tab."
  });
}

/** Record a consent audit entry (start/stop/enable/disable). */
async function auditConsent(action, meta = {}) {
  const entry = { ts: Date.now(), action, ...meta };
  let log = [];
  try {
    const raw = await chrome.storage.local.get(CONSENT_LOG_KEY);
    log = Array.isArray(raw[CONSENT_LOG_KEY]) ? raw[CONSENT_LOG_KEY] : [];
  } catch (_) {}
  log.push(entry);
  if (log.length > 1000) log = log.slice(-1000);
  await chrome.storage.local.set({ [CONSENT_LOG_KEY]: log });
}

/** Connect to the offscreen document's MediaRecorder. */
async function connectOffscreen() {
  await ensureOffscreen();
  return await chrome.runtime.connect({ name: "offscreen" });
}

/** Start recording the given tab. */
async function startRecording(tabId) {
  if (recordingSession) throw new Error("Already recording.");

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id !== tabId) {
    throw new Error("Recording is scoped to the active dashboard tab.");
  }

  const port = await connectOffscreen();
  port.postMessage({ type: "offscreen.start", tabId });

  let captureStarted = false;
  const captureReady = new Promise((resolve, reject) => {
    const handler = (msg) => {
      if (msg.type === "capture.started") { captureStarted = true; resolve(); }
      if (msg.type === "capture.error") { reject(new Error(msg.error)); }
    };
    port.onMessage.addListener(handler);
    setTimeout(() => { if (!captureStarted) reject(new Error("Capture timeout")); }, 5000);
  });
  await captureReady;
  port.onMessage.removeListener(() => {});

  const chunks = [];
  recordingSession = { tabId, startTime: Date.now(), chunks, events: [], port };

  chrome.runtime.onMessage.addListener(handleContentMessage);

  await auditConsent("start", { tabId, url: tab.url });

  await chrome.action.setBadgeText({ text: "REC", tabId });
  await chrome.action.setBadgeBackgroundColor({ color: "#dc2626", tabId });
  await broadcastRecordingState(true);

  return { tabId, startTime: recordingSession.startTime };
}

/** Stop recording, flush video + events to the dashboard backend. */
async function stopRecording() {
  const session = recordingSession;
  if (!session) return null;

  chrome.runtime.onMessage.removeListener(handleContentMessage);

  session.port.postMessage({ type: "offscreen.stop" });

  let chunks = [];
  const stopped = new Promise((resolve) => {
    const handler = (msg) => {
      if (msg.type === "offscreen.stopped") { chunks = msg.chunks; resolve(); }
    };
    session.port.onMessage.addListener(handler);
    session.port.disconnect();
    setTimeout(resolve, 5000);
  });
  await stopped;

  const blob = new Blob(chunks, { type: "video/webm" });
  const form = new FormData();
  form.append("video", blob, `record-${session.startTime}.webm`);
  form.append("events", JSON.stringify(session.events));
  form.append("tabId", String(session.tabId));
  form.append("startTime", String(session.startTime));
  form.append("endTime", String(Date.now()));
  form.append("extensionId", EXTENSION_ID);

  await fetch(RECORD_API, { method: "POST", body: form });

  await auditConsent("stop", {
    tabId: session.tabId,
    durationMs: Date.now() - session.startTime,
    eventCount: session.events.length
  });

  await chrome.action.setBadgeText({ text: "", tabId: session.tabId });

  const result = {
    tabId: session.tabId,
    startTime: session.startTime,
    endTime: Date.now(),
    eventCount: session.events.length
  };

  recordingSession = null;
  await broadcastRecordingState(false);
  return result;
}

/** Handle click events forwarded from the content script. */
function handleContentMessage(message, sender, sendResponse) {
  if (message.type !== "dashboard.interaction") return;
  if (!recordingSession || sender.tab.id !== recordingSession.tabId) return;
  recordingSession.events.push(sanitizeEventPayload(message.payload));
  sendResponse({ ok: true });
  return true;
}

/** Broadcast recording state to content/popup for UI indicator. */
async function broadcastRecordingState(recording) {
  try {
    await chrome.tabs.query({}, (tabs) => {
      for (const tab of tabs || []) {
        chrome.tabs.sendMessage(tab.id, { type: "extension.recordingState", recording }).catch(() => {});
      }
    });
  } catch (_) {}
}

chrome.action.onClicked.addListener(async (tab) => {
  const url = new URL(tab.url);
  if (url.origin !== DASHBOARD_ORIGIN) return;
  try {
    if (recordingSession) {
      await stopRecording();
    } else {
      await startRecording(tab.id);
    }
  } catch (err) {
    console.error("extension: recording toggle failed", err);
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "extension.isEnabled") {
    sendResponse({ ok: true, enabled: !!recordingSession || false });
    return true;
  }
  if (message.type === "extension.startRecording") {
    startRecording(sender.tab.id).then((r) => sendResponse({ ok: true, ...r })).catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (message.type === "extension.stopRecording") {
    stopRecording().then((r) => sendResponse({ ok: true, ...r })).catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  return false;
});

chrome.runtime.onInstalled.addListener(async () => {
  await chrome.action.setBadgeText({ text: "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#6b7280" });
});
