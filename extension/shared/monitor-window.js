/** A visible status surface, never an authority to start capture or the helper. */
export function createMonitorWindowController(api) {
  const url = api.runtime.getURL("monitor/monitor.html");
  let opening = null;
  let monitorError = "";
  let createdWindowId = null;
  const snapshot = () => ({ monitorError });
  api.windows?.onRemoved?.addListener((windowId) => {
    if (createdWindowId === windowId) createdWindowId = null;
  });

  async function existingWindow() {
    // getContexts survives service-worker recreation and does not require the
    // broad tabs permission. Do not infer identity from a title or cached ID.
    const contexts = await api.runtime.getContexts({ documentUrls: [url] });
    const existingId = contexts.find((context) => context.documentUrl === url &&
      Number.isInteger(context.windowId) && context.windowId >= 0)?.windowId;
    if (existingId !== undefined || createdWindowId === null) return existingId;
    // windows.create can resolve before its document appears in getContexts.
    // Validate only a window we created, so rapid sequential opens cannot race
    // that loading gap and create duplicates. An uncertain lookup fails closed.
    const ownWindow = await api.windows.get(createdWindowId, { populate: true });
    if (ownWindow?.tabs?.some((tab) => tab.url === url || tab.pendingUrl === url)) return createdWindowId;
    throw new Error("The previous status window is not yet identifiable. Recheck it or close it before reopening.");
  }

  function open() {
    if (opening) return opening;
    opening = (async () => {
      try {
        let windowId = await existingWindow();
        if (windowId !== undefined) {
          try { await api.windows.update(windowId, { focused: true, state: "normal" }); }
          catch (error) {
            // A user may close the window between lookup and focus. Recheck
            // before creating; another error must not produce a duplicate.
            windowId = await existingWindow();
            if (windowId !== undefined) throw error;
          }
        }
        if (windowId === undefined) {
          const created = await api.windows.create({ url, type: "popup", width: 400, height: 620, focused: true });
          if (!Number.isInteger(created?.id)) throw new Error("Browser did not confirm the status window was opened.");
          windowId = created.id;
          createdWindowId = windowId;
        }
        monitorError = "";
        return { ok: true, windowId, ...snapshot() };
      } catch (error) {
        monitorError = `Status window could not be opened: ${error.message || "Browser window controls are unavailable."} Use the toolbar popup to control Panther.`;
        return { ok: false, ...snapshot() };
      } finally { opening = null; }
    })();
    return opening;
  }

  // Window removal only invalidates its UI identity; it never changes power.
  return { open, snapshot };
}
