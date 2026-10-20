import { BACKEND_ORIGIN } from "../shared/constants.js";

const countEl = document.getElementById("count");
const emptyEl = document.getElementById("empty");
const tableEl = document.getElementById("recordingsTable");
const bodyEl = document.getElementById("recordingsBody");
const inferenceView = document.getElementById("inferenceView");
const inferenceEmpty = document.getElementById("inferenceEmpty");
const inferenceMeta = document.getElementById("inferenceMeta");
const inferenceBody = document.getElementById("inferenceBody");

let recordings = [];
let selectedId = null;

async function fetchRecordings() {
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/recordings`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    recordings = Array.isArray(data.recordings) ? data.recordings : [];
  } catch (err) {
    console.error("options: failed to fetch recordings", err);
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
    const tr = document.createElement("tr");
    const started = new Date(r.start_time * 1000).toLocaleString();
    const duration = (r.duration_ms / 1000).toFixed(1) + "s";
    const hasInf = r.has_inference ? "yes" : "no";
    tr.innerHTML = `
      <td>${started}</td>
      <td>${duration}</td>
      <td>${r.event_count}</td>
      <td><span class="inference-badge ${hasInf}">${hasInf === "yes" ? "✓" : "—"}</span></td>
      <td>
        <button class="btn-small btn-primary" onclick="app.openRecording('${r.id}')">View</button>
        <button class="btn-small btn-danger" onclick="app.deleteRecording('${r.id}')">Delete</button>
      </td>
    `;
    bodyEl.appendChild(tr);
  }
}

async function openRecording(id) {
  selectedId = id;
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/recordings/${id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const rec = await res.json();
    // show video link
    // inference
    showInference(id);
  } catch (err) {
    console.error("options: failed to open recording", err);
  }
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
    console.error("options: failed to fetch inference", err);
    inferenceEmpty.style.display = "block";
  }
}

async function deleteRecording(id) {
  if (!confirm("Delete this recording? This cannot be undone.")) return;
  try {
    const res = await fetch(`${BACKEND_ORIGIN}/api/extension/recordings/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (selectedId === id) {
      selectedId = null;
      inferenceView.style.display = "none";
      inferenceEmpty.style.display = "block";
    }
    await fetchRecordings();
  } catch (err) {
    console.error("options: failed to delete recording", err);
    alert("Failed to delete recording.");
  }
}

window.app = { openRecording, deleteRecording };

fetchRecordings();
