/** Offscreen document keeps MediaRecorder alive across service-worker suspension. */

let mediaRecorder = null;
let stream = null;
let chunks = [];
let port = null;

async function startCapture(tabId) {
  try {
    stream = await chrome.tabCapture.capture({ targetTabId: tabId, audio: false, video: true });
  } catch (err) {
    chrome.runtime.sendMessage({ type: "capture.error", error: String(err) });
    return;
  }
  if (!stream) {
    chrome.runtime.sendMessage({ type: "capture.error", error: "No stream" });
    return;
  }

  chunks = [];
  mediaRecorder = new MediaRecorder(stream, { mimeType: "video/webm" });
  mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  mediaRecorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
    mediaRecorder = null;
  };
  mediaRecorder.start(1000);

  chrome.runtime.sendMessage({ type: "capture.started", tabId });
}

async function stopCapture() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  mediaRecorder = null;
  stream = null;
  chunks = [];
}

chrome.runtime.onConnect.addListener((p) => {
  port = p;
  port.onMessage.addListener(async (msg) => {
    if (msg.type === "offscreen.start") {
      await startCapture(msg.tabId);
    } else if (msg.type === "offscreen.stop") {
      await stopCapture();
      if (port) port.postMessage({ type: "offscreen.stopped", chunks });
    }
  });
  port.onDisconnect.addListener(() => { port = null; });
});
