import { createCaptureController } from "../shared/capture.js";
import { createLifecycleController, trustedExtensionControl } from "../shared/lifecycle.js";
import { createMonitorWindowController } from "../shared/monitor-window.js";

let lifecycle;
const capture = createCaptureController(chrome, fetch, Date.now, { isPowered: () => lifecycle?.isOn() === true });
lifecycle = createLifecycleController(chrome, capture);
const monitor = createMonitorWindowController(chrome);
const powerCommands = new Set(["extension.turnOn", "extension.turnOff", "extension.getPowerState"]);
async function dispatch(message, sender) {
  if (message.type === "extension.openMonitor") {
    if (!trustedExtensionControl(chrome, sender)) return { ok: false, error: "Status window controls are only available in the extension." };
    return monitor.open();
  }
  if (message.type === "extension.turnOn" && trustedExtensionControl(chrome, sender)) {
    // Open immediately, not after native startup, so its Off button remains
    // reachable even if startup stalls. A window error never changes power.
    // Window errors are caught by the manager and exposed on subsequent
    // replies/polls. A stalled window API must not stall the native On reply.
    void monitor.open();
    return lifecycle.message(message, sender);
  }
  if (powerCommands.has(message.type)) return lifecycle.message(message, sender);
  if (message.type === "extension.getStatus" && trustedExtensionControl(chrome, sender)) {
    // Existing-port status detects a dead owned child. It never connects a
    // helper, probes an unrelated HTTP listener, or turns anything On.
    await lifecycle.message({ type: "extension.getPowerState" }, sender);
  }
  return capture.message(message, sender);
}
// Register synchronously so a suspended worker can wake for every event.
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (!message?.type?.startsWith("extension.") && !message?.type?.startsWith("page.")) return false;
  const operation = dispatch(message, sender);
  operation.then((result) => reply({ ...result, power: lifecycle.snapshot().power, ...monitor.snapshot() }),
    (error) => reply({ ok: false, error: error.message, power: lifecycle.snapshot().power, ...monitor.snapshot() }));
  return true;
});
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  capture.tabUpdated(tabId, change, tab).catch(console.error);
});
chrome.tabs.onRemoved.addListener((tabId) => { capture.tabRemoved(tabId).catch(console.error); });
chrome.runtime.onStartup.addListener(() => { capture.browserStarted().catch(console.error); });
chrome.runtime.onInstalled.addListener(() => { capture.browserStarted("extension_reloaded").catch(console.error); });
chrome.alarms.onAlarm.addListener((alarm) => { capture.alarm(alarm.name).catch(console.error); });
