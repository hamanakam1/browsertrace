import { BACKEND_ORIGIN } from "../shared/constants.js";

const origin = location.protocol === "chrome-extension:" ? BACKEND_ORIGIN : location.origin;
const $ = id => document.getElementById(id);
let selected = null;
let recordings = [];
let activePreview = null;
let loading = false;
let requestVersion = 0;

function message(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}
async function api(path, body, method = body === undefined ? "GET" : "POST") {
  const response = await fetch(origin + "/api/extension/" + path, {
    method, headers: { "Content-Type": "application/json", "X-Panther-Client": "review-v1" },
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    signal: AbortSignal.timeout(path.startsWith("tickets/") ? 125000 : 30000)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || result.message || result.state || "Request failed");
  return result;
}
function button(label, action, className = "") {
  const node = document.createElement("button");
  node.textContent = label; node.className = className;
  node.addEventListener("click", async () => {
    node.disabled = true;
    try { await action(); } catch (error) { message(error.message, true); }
    finally { node.disabled = false; }
  });
  return node;
}
function element(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text; node.className = className;
  return node;
}
async function refresh() {
  try {
    const health = await api("health");
    $("backendStatus").textContent = health.status === "ok" ? "Local backend connected" : "Backend needs attention";
    $("backendDot").classList.toggle("ok", health.status === "ok");
    recordings = (await api("recordings")).recordings;
    renderLibrary();
    message(recordings.length ? "Captures are ready to review." : "No captures yet. Open the extension on a web page to start.");
  } catch (error) {
    $("backendStatus").textContent = "Local backend unavailable";
    $("backendDot").classList.remove("ok");
    message(error.message + " — start backend/start.sh, then refresh.", true);
  }
  try {
    const status = await api("integrations/ai-dashboard");
    $("dashboardStatus").textContent = status.ok ? "AI Dashboard reachable · delivery verified when sent" : "AI Dashboard unavailable · JSON export still works";
  } catch (_) { $("dashboardStatus").textContent = "AI Dashboard status unavailable"; }
}
function renderLibrary() {
  $("count").textContent = recordings.length;
  $("recordings").replaceChildren();
  for (const capture of recordings) {
    const node = button(capture.title || capture.url || "Browser capture", () => openCapture(capture.id), "capture-choice");
    node.setAttribute("aria-current", String(selected?.id === capture.id));
    node.append(element("small", new Date(capture.start_time * 1000).toLocaleString() + " · " + capture.event_count + " events"));
    $("recordings").append(node);
  }
}
async function openCapture(id) {
  if (loading) return;
  const version = ++requestVersion;
  const [capture, evidence] = await Promise.all([api("recordings/" + id), api("recordings/" + id + "/events")]);
  if (version !== requestVersion) return;
  selected = capture;
  $("emptyReview").hidden = true; $("captureView").hidden = false;
  $("captureTitle").textContent = capture.title || "Browser capture";
  $("captureMeta").textContent = capture.url + " · " + capture.event_count + " events · " + (capture.duration_ms / 1000).toFixed(1) + " seconds";
  $("captureGoal").textContent = capture.goal ? "Your intent: " + capture.goal : "No intended outcome was supplied. Add the expected result to the draft.";
  $("events").replaceChildren();
  for (const event of evidence.events) {
    $("events").append(element("li", [
      event.type, event.target?.tag, event.target?.label || event.target?.text || event.target?.ariaLabel || "",
      event.url || event.page?.url || "", event.target?.dataTab ? "tab: " + event.target.dataTab : "",
      event.target?.dataTicket ? "ticket: " + event.target.dataTicket : ""
    ].filter(Boolean).join(" · ")));
  }
  $("screenshots").replaceChildren();
  $("screenshotsPanel").hidden = !capture.screenshot_count;
  if (capture.screenshot_count) {
    const shots = await api("recordings/" + id + "/screenshots");
    if (version !== requestVersion) return;
    for (const shot of shots.screenshots) {
      const img = document.createElement("img"); img.src = shot.dataUrl; img.alt = "Captured page evidence"; img.loading = "lazy";
      $("screenshots").append(img);
    }
  }
  $("generationStatus").textContent = "";
  try {
    const drafts = await api("tickets/" + id);
    if (version !== requestVersion) return;
    renderTickets(drafts, id);
  }
  catch (error) {
    if (version !== requestVersion) return;
    $("tickets").replaceChildren();
    $("generationStatus").textContent = error.message === "No drafts generated yet" ? "No drafts yet. Generate an evidence draft to start reviewing." : error.message;
  }
  renderLibrary();
  const url = new URL(location.href); url.searchParams.set("recording", id); history.replaceState(null, "", url);
}
async function generate(useAI) {
  if (!selected || loading) return;
  loading = true;
  $("generate").disabled = true; $("generateAI").disabled = true;
  $("generationStatus").textContent = useAI ? "Generating drafts with local AI… This can take up to two minutes." : "Preparing an evidence draft from captured interactions…";
  message("Draft generation in progress.");
  try {
    const data = await api("tickets/" + selected.id, {use_ai: useAI});
    renderTickets(data);
    message(data.tickets.length + " draft(s) ready for review. Nothing has been sent.");
  } catch (error) {
    $("generationStatus").textContent = "Generation failed: " + error.message + " Your capture is saved; you can retry.";
    message(error.message, true);
  } finally { loading = false; $("generate").disabled = false; $("generateAI").disabled = false; }
}
function renderTickets(data, recordingId = selected.id) {
  $("tickets").replaceChildren();
  $("generationStatus").textContent = data.tickets.length + " draft(s) · " +
    (data.mode === "ai" ? "Local AI suggestions — verify against evidence" : "Observed steps only — intended outcome needs review");
  for (const ticket of data.tickets) {
    const card = element("article", "", "ticket");
    card.append(element("span", data.mode === "ai" ? "AI PROPOSAL · UNVERIFIED" : "EVIDENCE DRAFT · REVIEW REQUIRED", "badge"));
    const fields = {};
    for (const [key, label, multiline] of [["title", "Ticket title", false], ["description", "Problem or improvement", true], ["acceptance_criteria", "Expected result / acceptance criteria (one per line)", true]]) {
      const id = "draft-" + ticket.id + "-" + key;
      const fieldLabel = element("label", label); fieldLabel.htmlFor = id;
      const input = document.createElement(multiline ? "textarea" : "input");
      input.id = id; input.maxLength = key === "title" ? 200 : key === "description" ? 2000 : 5000;
      input.value = key === "acceptance_criteria" ? (ticket[key] || []).join("\n") : ticket[key] || "";
      fields[key] = input; card.append(fieldLabel, input);
    }
    card.append(element("pre", "Observed steps:\n" + (ticket.steps_to_reproduce || []).map((s,i) => (i + 1) + ". " + s).join("\n")));
    card.append(element("p", ticket.evidence || "", "muted"));
    const actions = element("div", "", "actions");
    const patch = () => ({title: fields.title.value.trim(), description: fields.description.value.trim(), acceptance_criteria: fields.acceptance_criteria.value.split("\n").map(x => x.trim()).filter(Boolean)});
    actions.append(button("Save draft edits", async () => {
      await api("tickets/" + recordingId + "/review", {ticket_id: ticket.id, patch: patch()});
      message("Draft edits saved locally.");
    }));
    actions.append(button("Preview AI Dashboard ticket", async () => {
      const body = {ticket_id: ticket.id, patch: patch()};
      await api("tickets/" + recordingId + "/review", body);
      const preview = await api("integrations/ai-dashboard/preview/" + recordingId, body);
      activePreview = {id: recordingId, body, preview};
      const summary = $("previewSummary");
      summary.replaceChildren(element("h3", preview.payload.title), element("p", body.patch.description));
      summary.append(element("h3", "Expected result"));
      const criteria = element("ul", "");
      for (const value of body.patch.acceptance_criteria) criteria.append(element("li", value));
      summary.append(criteria);
      if (!body.patch.acceptance_criteria.length) summary.append(element("p", "No acceptance criteria supplied. Consider defining how this change will be verified.", "muted"));
      summary.append(element("h3", "Observed steps"));
      const steps = element("ol", "");
      for (const value of (ticket.steps_to_reproduce || []).slice(0, 10)) steps.append(element("li", value));
      summary.append(steps);
      $("previewBody").textContent = JSON.stringify(preview.payload, null, 2);
      $("previewWarnings").textContent = (preview.warnings || []).join(" ");
      $("deliveryStatus").textContent = "";
      $("confirmSend").disabled = false;
      $("previewDialog").showModal();
    }, "primary"));
    card.append(actions);
    $("tickets").append(card);
  }
}
$("refresh").addEventListener("click", refresh);
$("generate").addEventListener("click", () => generate(false));
$("generateAI").addEventListener("click", () => generate(true));
$("cancelSend").addEventListener("click", () => $("previewDialog").close());
$("confirmSend").addEventListener("click", async () => {
  if (!activePreview) return;
  $("confirmSend").disabled = true;
  $("deliveryStatus").classList.remove("error");
  $("deliveryStatus").textContent = "Sending and verifying the dashboard ticket…";
  try {
    const {id, body, preview} = activePreview;
    const result = await api("integrations/ai-dashboard/submit/" + id, {...body, confirmed: true, payload_hash: preview.payload_hash});
    if (result.verified) {
      const verifiedAt = result.receipt?.verified_at;
      const when = Number.isFinite(verifiedAt) ? new Date(verifiedAt * 1000).toLocaleString() : "the recorded delivery time";
      $("deliveryStatus").textContent = result.previously_verified
        ? "Previously verified in AI Dashboard: " + result.ticket_key + " at " + when + ". No duplicate sent; current ticket state has not been rechecked."
        : "Verified in AI Dashboard: " + result.ticket_key + " at " + when + ". The ticket had its no-auto review fence at verification.";
    } else {
      $("deliveryStatus").textContent = "Submission state: " + result.state + ". Verification is still pending; retry uses the same capture identity.";
    }
    if (result.verified && result.ticket_url) {
      const link = element("a", " Open ticket"); link.href = result.ticket_url; link.target = "_blank"; link.rel = "noopener"; $("deliveryStatus").append(link);
    }
  } catch (error) {
    $("deliveryStatus").textContent = error.message + " — Retry preserves the same ticket identity.";
    $("deliveryStatus").classList.add("error");
  } finally { $("confirmSend").disabled = false; }
});
$("deleteCapture").addEventListener("click", async () => {
  if (!selected || !confirm("Delete this capture and its local evidence? Tickets already sent to other systems remain there.")) return;
  try {
    await api("recordings/" + selected.id, undefined, "DELETE");
    selected = null; $("captureView").hidden = true; $("emptyReview").hidden = false; await refresh();
  } catch (error) { message(error.message, true); }
});
$("export").addEventListener("click", async () => {
  if (!selected) return;
  try {
    const bundle = await api("export/" + selected.id);
    const blob = new Blob([JSON.stringify(bundle, null, 2)], {type: "application/json"});
    const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = "panther-" + selected.id + ".json"; link.click(); URL.revokeObjectURL(url);
    message("Draft bundle exported. Save any draft edits before exporting.");
  } catch (error) { message(error.message, true); }
});
await refresh();
const initial = new URL(location.href).searchParams.get("recording");
if (initial && recordings.some(r => r.id === initial)) await openCapture(initial);
