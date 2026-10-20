# Roadmap — Dashboard Intelligence Extension

## Phase 1 — Edge MVP (complete)
- [x] Manifest V3 extension for Microsoft Edge
- [x] Opt-in consent gate (default off)
- [x] Dashboard-tab-scoped video capture via `tabCapture`
- [x] Click / keyboard / change / submit interaction capture
- [x] Secret/credential redaction (extension + backend)
- [x] Visible recording indicator badge
- [x] Bundle video + events → POST to standalone backend `/api/extension/record`
- [x] Consent audit log
- [x] Standalone Python backend (:7890) with record/list/get/delete APIs
- [x] Options page: recordings library + inference results + delete

## Phase 2 — Inference pipeline (complete)
- [x] `POST /api/extension/infer/:id` on the backend
- [x] Sample recording frames via ffmpeg, run Ollama (`qwen3.8-hermes:latest`)
- [x] Correlate inference summary with click timeline
- [x] Inference results stored and retrievable via `GET /api/extension/infer/:id`
- [x] 30-day TTL + cap on stored recordings

## Phase 3 — Polish (complete)
- [x] Serve video files from backend (`GET /api/extension/recordings/:id/video`)
- [x] HTTP Range request support for video seeking (206 Partial Content)
- [x] Play video in options page player
- [x] Backend health check + startup script (`backend/start.sh`)
- [x] Launchd service plist for production (`com.michaelhamanaka.dashboard-extension-backend`)
- [x] Backend-unavailable banner in popup, record disabled when down

## Phase 4 — Cross-browser (complete)
- [x] Google Chrome (Manifest V3) — `manifest.chrome.json`
- [x] Firefox (WebExtension V3) — `manifest.firefox.json` with `browser_specific_settings`
- [x] Shared core content/background/options code across all targets
- [ ] Safari (Safari Web Extension) — separate build target
- [ ] CI/build step that packages each browser's `.zip`/`.crx`/`.xpi`

## Phase 5 — Later features (backlog)
- [ ] Real-time / streaming inference with live overlay
- [ ] Click heatmaps & session-replay DOM reconstruction
- [ ] Encrypted export for sharing
- [ ] Multi-session comparison & anomaly detection on click patterns
- [ ] Opt-in end-to-end-encrypted remote/team sync

## Design principles (never change)
1. **Opt-in only.** Recording is off by default; explicit consent per session.
2. **Dashboard-scoped.** Capture only the dashboard tab; never arbitrary sites; never full desktop by default.
3. **Local-first.** Recordings and inference stay on the operator's machine.
4. **No secrets.** Password/credential fields are redacted before storage.
5. **Visible indicator.** A persistent badge shows while recording.
6. **User deletes.** Recordings carry a TTL; the user can delete any recording.
