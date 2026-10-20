# Roadmap — Dashboard Intelligence Extension

## Phase 1 — Edge MVP (now)
- Manifest V3 extension for Microsoft Edge
- Opt-in consent gate (default off)
- Dashboard-tab-scoped video capture via `tabCapture`
- Click / keyboard / change / submit interaction capture
- Redaction of secret/credential fields
- Visible recording indicator badge
- Bundle video + events → POST to dashboard backend `/api/extension/record`
- Consent audit log

## Phase 2 — Inference pipeline
- `POST /api/extension/infer/:id` on the dashboard backend
- Sample recording frames, run local inference (Ollama `:11434` / MLX `:8081`)
- Correlate inference summary with click timeline
- Surfaced results in a dashboard Extension tab

## Phase 3 — Dashboard UI integration
- Extension group/tab in `templates/index.html` nav
- Recordings library: play video, view click timeline, delete
- Consent + settings panel
- Docs guide in `docs/help/`

## Phase 4 — Cross-browser
- Google Chrome (Manifest V3)
- Firefox (WebExtension)
- Safari (Safari Web Extension)
- Shared core logic, per-browser adapters

## Phase 5 — Later features (backlog)
- Real-time / streaming inference with live overlay
- Click heatmaps & session-replay DOM reconstruction
- Encrypted export for sharing
- Multi-session comparison & anomaly detection on click patterns
- Opt-in end-to-end-encrypted remote/team sync

## Design principles (never change)
1. **Opt-in only.** Recording is off by default; explicit consent per session.
2. **Dashboard-scoped.** Capture only the dashboard tab; never arbitrary sites; never full desktop by default.
3. **Local-first.** Recordings and inference stay on the operator's machine.
4. **No secrets.** Password/credential fields are redacted before storage.
5. **Visible indicator.** A persistent badge shows while recording.
6. **User deletes.** Recordings carry a TTL; the user can delete any recording.
