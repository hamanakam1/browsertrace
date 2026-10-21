export const NATIVE_HOST = "com.panther.intent_capture";

export function trustedExtensionControl(api, sender) {
  if (sender?.id !== api.runtime.id) return false;
  try {
    const source = new URL(sender.url);
    const extension = new URL(api.runtime.getURL(""));
    return source.protocol === extension.protocol && source.host === extension.host &&
      !source.username && !source.password &&
      ["/popup/popup.html", "/options/options.html"].includes(source.pathname);
  } catch { return false; }
}

/** The native port is a browser-owned lifetime lease, never a login service. */
export function createLifecycleController(api, capture, { timeoutMs = 30000, persistenceTimeoutMs = 2000 } = {}) {
  let power = { state: "off", error: "", configured: false };
  let port = null;
  let epoch = 0;
  let nextId = 0;
  let onPromise = null;
  let offPromise = null;
  let statusPromise = null;
  let shutdownUnconfirmed = false;
  const pending = new Map();
  const snapshot = (ok = true) => ({ ok, power: { ...power } });
  const setupError = (detail) => `Native helper unavailable. Install/register ${NATIVE_HOST} for this browser and extension ID ${api.runtime.id}. ${detail || "No helper response."}`;
  const freeze = (reason) => capture.freeze(reason);
  const ready = freeze("Panther is Off until you explicitly turn it On.").catch((error) => {
    power = { ...power, state: "error", error: `Could not freeze local capture: ${error.message}` };
  });

  function rejectPort(current, error) {
    for (const [id, request] of pending) if (request.port === current) {
      clearTimeout(request.timer);
      pending.delete(id);
      request.reject(error);
    }
  }

  function release(current) {
    if (!current) return;
    if (port === current) port = null;
    rejectPort(current, new Error("Native connection closed."));
    try { current.disconnect(); } catch {}
  }

  function connect() {
    // This function is called only by an explicit, trusted turnOn command.
    const current = api.runtime.connectNative(NATIVE_HOST);
    port = current;
    shutdownUnconfirmed = true;
    current.onMessage.addListener((message) => {
      const request = pending.get(message?.id);
      if (!request || request.port !== current) return;
      clearTimeout(request.timer);
      pending.delete(message.id);
      if (typeof message.ok !== "boolean" || !["on", "off", "error"].includes(message.state)) {
        request.reject(new Error("Native helper returned an invalid status."));
      } else if (!message.ok || message.state === "error") {
        request.reject(new Error(String(message.error || "Native helper reported an error.")));
      } else request.resolve(message);
    });
    current.onDisconnect.addListener(() => {
      // Consume lastError in this callback, including an intentional old-port
      // disconnect, to avoid Chrome's unchecked-runtime-error diagnostics.
      const detail = api.runtime.lastError?.message || "Native helper disconnected.";
      rejectPort(current, new Error(detail));
      if (port !== current) return;
      port = null;
      epoch++;
      shutdownUnconfirmed = true;
      power = { ...power, state: "error", error: power.configured ?
        `${detail} Capture is frozen. Backend shutdown and in-flight delivery are not yet verified. Turn On explicitly to reconnect.` : setupError(detail) };
      freeze(power.error).catch(() => {});
    });
    return current;
  }

  function request(current, action, timeout = timeoutMs) {
    return new Promise((resolve, reject) => {
      const id = `panther-${++nextId}`;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Native helper ${action} timed out. Its backend state is unconfirmed.`));
      }, timeout);
      pending.set(id, { port: current, resolve, reject, timer });
      try { current.postMessage({ id, action }); }
      catch (error) { clearTimeout(timer); pending.delete(id); reject(error); }
    });
  }

  function settleFreeze(promise) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve("Local capture persistence is still pending; events remain frozen in memory. Recheck before closing the browser."), persistenceTimeoutMs);
      promise.then(() => { clearTimeout(timer); resolve(""); }, (error) => {
        clearTimeout(timer);
        resolve(`Local capture persistence failed: ${error.message}. Events remain frozen in memory; recheck before closing the browser.`);
      });
    });
  }

  async function turnOn() {
    if (/preview/i.test(api.runtime.getManifest?.().name || "")) {
      power = { state: "error", configured: false, error: "Browser-controlled On/Off is supported only in the Chrome / Edge build. This browser manifest is an unsupported preview." };
      return snapshot(false);
    }
    if (power.state === "on") return snapshot();
    if (onPromise && power.state === "starting") return onPromise;
    if (power.state === "stopping") return { ...snapshot(false), error: "Wait for Turn Off to finish before starting again." };
    const token = ++epoch;
    power = { ...power, state: "starting", error: "" };
    onPromise = (async () => {
      let current;
      try {
        await ready;
        await freeze("Starting Panther; capture consent must be selected again.");
        if (token !== epoch) return snapshot(false);
        current = connect();
        const response = await request(current, "start");
        if (token !== epoch || port !== current) return snapshot(false);
        if (response.state !== "on") throw new Error("Native helper did not confirm that its backend is On.");
        power = { state: "on", error: "", configured: true };
        shutdownUnconfirmed = false;
        capture.unlock();
        return snapshot();
      } catch (error) {
        if (token !== epoch) return snapshot(false);
        release(current);
        power = { state: "error", configured: power.configured, error: setupError(error.message) };
        await freeze(power.error);
        return snapshot(false);
      } finally { if (token === epoch) onPromise = null; }
    })();
    return onPromise;
  }

  function turnOff() {
    if (offPromise && power.state === "stopping") return offPromise;
    const token = ++epoch;
    const current = port;
    power = { ...power, state: "stopping", error: "" };
    // freeze() revokes authority synchronously, before waiting on storage,
    // network cancellation, an in-flight Start, or native shutdown.
    const frozen = freeze("Panther turned Off. Events are retained; any accepted upload or in-flight ticket may still exist. Verify after turning On.");
    const persistence = settleFreeze(frozen);
    // Do not let a stalled storage, injection or tab-message promise keep the
    // owned backend delivering after Off. The synchronous freeze above has
    // already revoked authority; durable local cleanup settles independently.
    const shutdown = current ? request(current, "stop") : Promise.resolve({ state: "off" });
    offPromise = (async () => {
      try {
        const response = await shutdown;
        if (response.state !== "off") throw new Error("Native helper did not confirm its backend stopped.");
        if (current) shutdownUnconfirmed = false;
        if (token !== epoch) return snapshot(false);
        if (!current && shutdownUnconfirmed) {
          power = { ...power, state: "error", error: "Capture is frozen, but prior backend shutdown remains unconfirmed. No connected helper can verify it; Turn On explicitly to establish a new owned backend. External services were not inspected or stopped." };
          return snapshot(false);
        }
        release(current);
        const persistenceError = await persistence;
        if (token !== epoch) return snapshot(false);
        power = { ...power, state: "off", error: persistenceError };
        if (persistenceError) frozen.then(() => {
          if (token === epoch && power.state === "off" && power.error === persistenceError) power = { ...power, error: "" };
        }, () => {});
        return snapshot();
      } catch (error) {
        if (token !== epoch) return snapshot(false);
        shutdownUnconfirmed = true;
        release(current);
        power = { ...power, state: "error", error: `Capture is frozen, but backend shutdown is unconfirmed: ${error.message}` };
        return snapshot(false);
      } finally { if (token === epoch) offPromise = null; }
    })();
    return offPromise;
  }

  async function getPowerState() {
    await ready;
    if (!port || power.state !== "on") return snapshot();
    if (statusPromise) return statusPromise;
    const current = port, token = epoch;
    statusPromise = (async () => {
      try {
        const response = await request(current, "status", Math.min(timeoutMs, 5000));
        if (token !== epoch || port !== current) return snapshot();
        if (response.state !== "on") throw new Error("The browser-owned backend is no longer On.");
        return snapshot();
      } catch (error) {
        if (token !== epoch || port !== current) return snapshot();
        epoch++;
        shutdownUnconfirmed = true;
        power = { ...power, state: "error", error: `${error.message} Capture is frozen; Turn On explicitly to reconnect.` };
        release(current);
        await freeze(power.error);
        return snapshot(false);
      } finally { statusPromise = null; }
    })();
    return statusPromise;
  }

  return {
    snapshot,
    isOn: () => power.state === "on" && port !== null,
    message: (message, sender) => {
      if (!trustedExtensionControl(api, sender)) return Promise.resolve({ ...snapshot(false), error: "Power controls are only available in the extension." });
      if (message.type === "extension.turnOn") return turnOn();
      if (message.type === "extension.turnOff") return turnOff();
      if (message.type === "extension.getPowerState") return getPowerState();
      return Promise.resolve({ ...snapshot(false), error: "Unknown power command." });
    }
  };
}
