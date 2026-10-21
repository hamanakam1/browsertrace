/* Classic script: executeScript files cannot contain module imports. */
(() => {
  if (globalThis.__pantherCaptureInstalled) return;
  globalThis.__pantherCaptureInstalled = true;
  let sessionId = null;
  let lastUrl = "";
  let routeTimer = null;
  let statusTimer = null;
  let panel = null;
  let statusText, detailsText, stopButton, reviewButton;

  function safeUrl(value) {
    try {
      const url = new URL(value, location.href);
      if (!/^https?:$/.test(url.protocol)) return "";
      url.username = ""; url.password = ""; url.search = "";
      if (!/^#\/?[a-z][a-z0-9_/-]{0,100}$/i.test(url.hash) || /secret|token|key|pass|cred|auth|cookie|session/i.test(url.hash)) url.hash = "";
      url.pathname = url.pathname.split("/").map((part, i, parts) =>
        /secret|token|key|pass|cred|auth|cookie|session/i.test(decodeURIComponent(parts[i - 1] || "")) || part.length > 48 ? "REDACTED" : part).join("/");
      return url.href;
    } catch { return ""; }
  }

  function send(type, target = null) {
    if (!sessionId) return;
    const semantic = target?.closest?.("button,a,input,select,textarea,form,[role=button],[role=tab],[data-tab],[data-ticket]");
    if (target && !semantic) return;
    const payload = { type, ts: Date.now(), url: safeUrl(location.href) };
    if (semantic) {
      const tag = semantic.tagName.toLowerCase();
      // Never inspect input values, typed keys, page body text, or editable text.
      const hasEditableContent = semantic.isContentEditable || semantic.querySelector("input,textarea,select,[contenteditable],[role=textbox]");
      const label = ["button", "a"].includes(tag) && !hasEditableContent ?
        (semantic.getAttribute("aria-label") || semantic.textContent || "").trim().slice(0, 120) : "";
      payload.target = {
        tag, role: semantic.getAttribute("role") || "", id: semantic.id || "",
        type: semantic.getAttribute("type") || "", label,
        href: tag === "a" ? safeUrl(semantic.href) : "",
        dataTab: semantic.getAttribute("data-tab") || "",
        dataTicket: semantic.getAttribute("data-ticket") || ""
      };
    }
    chrome.runtime.sendMessage({ type: "page.interaction", sessionId, payload }).then((reply) => {
      if (!reply?.ok) {
        stopListening();
        if (detailsText && reply?.error) detailsText.textContent = reply.error;
      }
      if (reply?.eventCount !== undefined && detailsText) detailsText.textContent = `${reply.eventCount} events · this tab only${reply.warning ? ` · ${reply.warning}` : ""}`;
    }).catch(() => stopListening());
  }

  function routeChanged() {
    if (sessionId && location.href !== lastUrl) {
      lastUrl = location.href;
      send("navigation");
    }
  }

  function stopListening() {
    sessionId = null;
    clearInterval(routeTimer);
    routeTimer = null;
  }

  function hidePanel() {
    panel?.remove();
    panel = null;
    clearInterval(statusTimer);
    statusTimer = null;
  }

  function renderStatus(state) {
    if (!panel || !state?.ok) return;
    const labels = { recording: "Capturing", saving: "Saving recording…", pending: "Upload needs retry", generating: "Generating drafts…", ready: "Drafts ready", error: "Generation needs retry", idle: "Idle" };
    statusText.textContent = `Panther · ${labels[state.status] || state.status}`;
    const details = state.session?.error || state.result?.error ||
      (state.status === "ready" ? `${state.result.ticketCount} reviewable draft${state.result.ticketCount === 1 ? "" : "s"} · ${state.result.eventCount} events` :
        `${state.session?.eventCount ?? state.result?.eventCount ?? 0} events · this tab only`);
    const warning = state.session?.warning || state.result?.warning;
    detailsText.textContent = `${details}${warning ? ` · ${warning}` : ""}`;
    stopButton.hidden = state.status !== "recording";
    stopButton.disabled = false;
    reviewButton.hidden = !state.result?.id;
    if (state.status !== "recording") stopListening();
  }

  async function refreshStatus() {
    try { renderStatus(await chrome.runtime.sendMessage({ type: "page.getStatus" })); }
    catch { if (statusText) statusText.textContent = "Panther · reopen extension to check status"; }
  }

  function showPanel() {
    hidePanel();
    panel = document.createElement("div");
    panel.id = "panther-intent-capture-controls";
    panel.style.cssText = "all:initial!important;position:fixed!important;bottom:18px!important;right:18px!important;z-index:2147483647!important;display:block!important;";
    const shadow = panel.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = ".box{box-sizing:border-box;width:270px;padding:14px;background:#111827;color:#f9fafb;border:1px solid #475569;border-radius:12px;box-shadow:0 8px 30px #0005;font:13px/1.4 system-ui}strong{display:block;font-size:14px}p{margin:7px 0 10px;color:#cbd5e1;overflow-wrap:anywhere}nav{display:flex;gap:6px;flex-wrap:wrap}button{font:600 12px system-ui;padding:7px 9px;border-radius:6px;border:1px solid #64748b;background:#334155;color:white;cursor:pointer}button[hidden]{display:none}button:disabled{opacity:.5}.stop{background:#b91c1c;border-color:#f87171}.hide{margin-left:auto}";
    const box = document.createElement("section");
    box.className = "box";
    box.setAttribute("aria-label", "Panther capture controls");
    statusText = document.createElement("strong");
    statusText.setAttribute("role", "status");
    statusText.textContent = "Panther · Capturing";
    detailsText = document.createElement("p");
    detailsText.textContent = "0 events · this tab only";
    const controls = document.createElement("nav");
    stopButton = document.createElement("button");
    stopButton.type = "button"; stopButton.className = "stop"; stopButton.textContent = "Stop & generate";
    stopButton.addEventListener("click", async () => {
      stopButton.disabled = true;
      statusText.textContent = "Panther · Saving recording…";
      try {
        const response = await chrome.runtime.sendMessage({ type: "page.stop", sessionId });
        if (response?.status) renderStatus({ ...response, ok: true });
        else { statusText.textContent = "Panther · Stop needs attention"; detailsText.textContent = response?.error || "Check the extension for capture status."; }
      } catch { statusText.textContent = "Panther · Check the extension for upload status"; }
    });
    reviewButton = document.createElement("button");
    reviewButton.type = "button"; reviewButton.textContent = "Review drafts"; reviewButton.hidden = true;
    reviewButton.addEventListener("click", () => { chrome.runtime.sendMessage({ type: "page.openReview" }).catch(() => {}); });
    const hide = document.createElement("button");
    hide.type = "button"; hide.className = "hide"; hide.textContent = "Hide";
    hide.title = "Hide this panel; capture continues. Use the extension icon to stop.";
    hide.addEventListener("click", hidePanel);
    controls.append(stopButton, reviewButton, hide);
    box.append(statusText, detailsText, controls);
    shadow.append(style, box);
    document.documentElement.appendChild(panel);
    statusTimer = setInterval(refreshStatus, 1000);
  }

  function enable(id) {
    if (sessionId === id) return;
    stopListening();
    sessionId = id;
    lastUrl = location.href;
    showPanel();
    // pushState is not observable from an isolated content world. Poll only
    // during active capture to include SPA routers and dashboard hash routes.
    routeTimer = setInterval(routeChanged, 500);
    send("navigation");
  }

  for (const type of ["click", "change", "submit"]) {
    document.addEventListener(type, (event) => {
      if (event.isTrusted && !event.composedPath().includes(panel)) send(type, event.target);
    }, true);
  }
  window.addEventListener("hashchange", routeChanged);
  window.addEventListener("popstate", routeChanged);
  // Every event is sent immediately, with no unload-time batch or Blob message.
  chrome.runtime.onMessage.addListener((message, _sender, reply) => {
    if (message.type === "extension.captureStart") {
      enable(message.sessionId);
      reply({ ok: true });
    } else if (message.type === "extension.captureStop") {
      stopListening();
      refreshStatus();
      reply({ ok: true });
    }
  });
})();
