# Roadmap — panther-intent-capture

## Phase 1 — Edge MVP (complete)
- [x] Manifest V3 extension for Microsoft Edge
- [x] Opt-in consent gate (default off)
- [x] Page-tab-scoped video capture via `tabCapture`
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

## Phase 3 — Intent decomposition → tickets (in progress)
- [ ] Decompose recorded session into named intents (goals)
- [ ] Generate a ticket per intent: description, steps to reproduce, owner
- [ ] Attach screenshots captured at decision points to each ticket
- [ ] Emit tickets in a structured format coding agents can consume
- [ ] Audio capture alongside video (planned)

## Phase 4 — Coding agent action (backlog)
- [ ] Coding agent reads tickets and takes action (create issues, write code, run tests, deploy)
- [ ] Real-time / streaming inference with live overlay
- [ ] Click heatmaps & session-replay DOM reconstruction
- [ ] Encrypted export for sharing recordings
- [ ] Multi-session comparison & anomaly detection on click patterns

## Phase 5 — Cross-browser (backlog)
- [ ] Safari (Safari Web Extension) — separate build target
- [ ] CI/build step that packages each browser's `.zip`/`.crx`/`.xpi`

## Design principles (never change)
1. **Opt-in only.** Recording is off by default; explicit consent per session.
2. **Page-scoped.** Capture only pages you configure; never arbitrary sites; never full desktop by default.
3. **Local-first.** Recordings and inference stay on the operator's machine.
4. **No secrets.** Password/credential fields are redacted before storage.
5. **Visible indicator.** A persistent badge shows while recording.
6. **User deletes.** Recordings carry a TTL; the user can delete any recording.
