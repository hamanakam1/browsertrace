import { BACKEND_ORIGIN } from "../shared/constants.js";

const countEl = document.getElementById("count");
const emptyEl = document.getElementById("empty");
const tableEl = document.getElementById("recordingsTable");
const bodyEl = document.getElementById("recordingsBody");
const viewPanel = document.getElementById("viewPanel");
const viewTitle = document.getElementById("viewTitle");
const viewMeta = document.getElementById("viewMeta");
const recVideo = document.getElementById("recVideo");
const inferenceView = document.getElementById("inferenceView");
const inferenceEmpty = document.getElementById("inferenceEmpty");
const inferenceMeta = document.getElementById("inferenceMeta");
const inferenceBody = document.getElementById("inferenceBody");
const ticketsView = document.getElementById("ticketsView");
const ticketsEmpty = document.getElementById("ticketsEmpty");
const ticketsMeta = document.getElementById("ticketsMeta");
const ticketsBody = document.getElementById("ticketsBody");

let recordings = [];
let selectedId = null;

async function fetchRecordings() {
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/recordings`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    recordings = Array.isArray(data.recordings) ? data.recordings : [];
  } catch (err) {
    console.error("panther-intent-capture: failed to fetch recordings", err);
    recordings = [];
  }
  render();
}

function render() {
  countEl.textContent = recordings.length;
  const has = recordings.length > 0;
  emptyEl.style.display = has ? "none" : "block";
  tableEl.style.display = has ? "table" : "none";

  bodyEl.innerHTML = "";
  for (const r of recordings) {
    const started = new Date(r.start_time * 1000).toLocaleString();
    const duration = (r.duration_ms / 1000).toFixed(1) + "s";
    const hasInf = r.has_inference ? "yes" : "no";
    const url = r.url || "—";
    const shortUrl = url.length > 40 ? url.slice(0, 37) + "…" : url;
    const screenshots = r.screenshot_count || 0;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${started}</td>
      <td title="${esc(url)}">${shortUrl}</td>
      <td>${duration}</td>
      <td>${r.event_count}</td>
      <td>${screenshots}</td>
      <td><span class="inference-badge ${hasInf}">${hasInf === "yes" ? "✓" : "—"}</span></td>
      <td><button class="btn-small btn-tickets" onclick="app.generateTickets('${r.id}')">${r.has_tickets ? "View" : "Generate"}</button></td>
      <td>
        <button class="btn-small" onclick="app.playRecording('${r.id}')">Play</button>
        <button class="btn-small btn-danger" onclick="app.deleteRecording('${r.id}')">Delete</button>
      </td>
    `;
    bodyEl.appendChild(tr);
  }
}

function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[m])); }

async function playRecording(id) {
  selectedId = id;
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/recordings/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const rec = await res.json();

    viewTitle.textContent = `Recording · ${new Date(rec.start_time * 1000).toLocaleString()}`;
    viewMeta.innerHTML = `
      URL: <b>${esc(rec.url || "—")}</b> &middot;
      Duration: <b>${(rec.duration_ms / 1000).toFixed(1)}s</b> &middot;
      Events: <b>${rec.event_count}</b> &middot;
      Screenshots: <b>${rec.screenshot_count || 0}</b>
    `;
    recVideo.src = `${BACKEND_ORIGIN}/api/extension/recordings/${id}/video`;

    viewPanel.style.display = "block";
    ticketsView.style.display = "none";
    inferenceView.style.display = "none";
    inferenceEmpty.style.display = "none";
    ticketsEmpty.style.display = "none";

    await showInference(id);
    await showTickets(id);
  } catch (err) {
    console.error("panther-intent-capture: failed to open recording", err);
    alert("Failed to load recording.");
  }
}

function closeView() {
  viewPanel.style.display = "none";
  recVideo.src = "";
  recVideo.pause();
  selectedId = null;
  inferenceView.style.display = "none";
  inferenceEmpty.style.display = "none";
  ticketsView.style.display = "none";
  ticketsEmpty.style.display = "none";
}

async function showInference(id) {
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/infer/${id}`);
    if (res.status === 404) {
      inferenceView.style.display = "none";
      inferenceEmpty.style.display = "block";
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    inferenceEmpty.style.display = "none";
    inferenceView.style.display = "block";
    const result = data.result || {};
    inferenceMeta.textContent = `Model: ${result.model || "—"} · Frames: ${result.frames_sampled || 0} · Events: ${result.events_count || 0} · Took ${result.duration_seconds || 0}s`;
    inferenceBody.textContent = result.narrative || "No narrative available.";
  } catch (err) {
    console.error("panther-intent-capture: failed to fetch inference", err);
    inferenceEmpty.style.display = "block";
  }
}

async function showTickets(id) {
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/tickets/${id}`);
    if (res.status === 404) {
      ticketsView.style.display = "none";
      ticketsEmpty.style.display = "block";
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    ticketsEmpty.style.display = "none";
    ticketsView.style.display = "block";
    const tickets = data.tickets || [];
    ticketsMeta.textContent = `${tickets.length} intent ticket(s) · ${data.cached ? "cached" : "fresh"}`;
    ticketsBody.innerHTML = "";
    for (const t of tickets) {
      const ticketEl = document.createElement("div");
      ticketEl.className = "ticket";
      ticketEl.innerHTML = `
        <div class="ticket-header">
          <span class="ticket-badge priority-${t.priority || 'medium'}">${t.priority || 'medium'}</span>
          <span class="ticket-badge component">${t.component || 'unspecified'}</span>
        </div>
        <h4 style="margin:4px 0 2px 0; font-size:14px;">${esc(t.title || "Untitled")}</h4>
        <p style="font-size:12px; color:#4b5563; margin:0 0 6px 0;">${esc(t.description || "")}</p>
        <div style="font-size:11px; margin-bottom:6px;"><strong>Steps:</strong> ${Array.isArray(t.steps_to_reproduce) ? t.steps_to_reproduce.map(s => esc(String(s))).join(" → ") : ""}</div>
        <div style="font-size:11px; color:#6b7280; margin-bottom:6px;"><strong>Evidence:</strong> ${esc(t.evidence || "")}</div>
        ${Array.isArray(t.screenshots) && t.screenshots.length > 0 ? `<div style="font-size:11px; color:#6b7280;"><strong>Screenshot frames:</strong> ${t.screenshots.join(", ")}</div>` : ""}
      `;
      ticketsBody.appendChild(ticketEl);
    }
  } catch (err) {
    console.error("panther-intent-capture: failed to fetch tickets", err);
    ticketsEmpty.style.display = "block";
  }
}

async function generateTickets(id) {
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/tickets/${id}`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    await showTickets(id);
  } catch (err) {
    console.error("panther-intent-capture: failed to generate tickets", err);
    alert("Failed to generate tickets.");
  }
}

async function deleteRecording(id) {
  if (!confirm("Delete this recording? This cannot be undone.")) return;
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/recordings/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (selectedId === id) closeView();
    await fetchRecordings();
  } catch (err) {
    console.error("panther-intent-capture: failed to delete recording", err);
    alert("Failed to delete recording.");
  }
}

window.app = { playRecording, closeView, deleteRecording, generateTickets, showTickets };

fetchRecordings();
