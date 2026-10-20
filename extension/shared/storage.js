const STORAGE_KEY = "extension.local.state";

export async function getState() {
  try {
    const raw = await chrome.storage.local.get(STORAGE_KEY);
    return raw[STORAGE_KEY] || {};
  } catch (_) {
    return {};
  }
}

export async function setState(patch) {
  try {
    const current = await getState();
    await chrome.storage.local.set({ [STORAGE_KEY]: { ...current, ...patch } });
  } catch (_) {
    // Storage unavailable; best effort.
  }
}

export async function clearState() {
  try {
    await chrome.storage.local.remove(STORAGE_KEY);
  } catch (_) {}
}
