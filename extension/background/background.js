import { createCaptureController } from "../shared/capture.js";

const capture = createCaptureController(chrome, fetch);
// Register synchronously so a suspended worker can wake for every event.
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (!message?.type?.startsWith("extension.") && !message?.type?.startsWith("page.")) return false;
  capture.message(message, sender).then(reply, (error) => reply({ ok: false, error: error.message }));
  return true;
});
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  capture.tabUpdated(tabId, change, tab).catch(console.error);
});
chrome.tabs.onRemoved.addListener((tabId) => { capture.tabRemoved(tabId).catch(console.error); });
chrome.runtime.onStartup.addListener(() => { capture.browserStarted().catch(console.error); });
chrome.runtime.onInstalled.addListener(() => { capture.browserStarted("extension_reloaded").catch(console.error); });
chrome.alarms.onAlarm.addListener((alarm) => { capture.alarm(alarm.name).catch(console.error); });
