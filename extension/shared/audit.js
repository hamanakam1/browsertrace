let log = [];
const STORAGE_KEY = "extension.consent.log";
const MAX_LOG = 1000;

export async function recordConsentEvent(action, meta = {}) {
  const entry = {
    ts: Date.now(),
    action,
    ...meta
  };
  log.push(entry);
  if (log.length > MAX_LOG) log = log.slice(-MAX_LOG);
  try {
    await chrome.storage.local.set({ [STORAGE_KEY]: log });
  } catch (_) {
    // Storage unavailable in some contexts; keep in-memory.
  }
}

export async function loadConsentLog() {
  try {
    const raw = await chrome.storage.local.get(STORAGE_KEY);
    return Array.isArray(raw[STORAGE_KEY]) ? raw[STORAGE_KEY] : [];
  } catch (_) {
    return log.slice();
  }
}
