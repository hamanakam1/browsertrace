# dashboard-extension

Standalone browser extension (Manifest V3) that observes dashboard usage and acts on that intelligence. Built first for Microsoft Edge, with Chrome, Firefox, and Safari targets planned.

The extension records, with explicit user consent, the video of the dashboard tab and the clicks/interactions within it, then runs inference against a local inference fleet to surface insights about how the dashboard is being used.

## Scope

- **Opt-in only.** Recording never starts without explicit per-session consent. Default is off.
- **Dashboard-scoped.** Captures only the dashboard tab (`tabCapture`), never arbitrary websites, never full desktop by default.
- **Local-first.** Recordings, click events, and inference results live on the operator's machine under `~/.dashboard-extension/`. Nothing leaves the machine unless the operator explicitly exports it.
- **No secrets.** Credential/password/secret inputs are scrubbed before anything is stored.
- **Visible indicator.** A persistent badge shows while recording; the browser enforces a tab indicator for `tabCapture`.

## Browser targets

| Browser | Manifest | Status |
|---|---|---|
| Microsoft Edge | V3 | Phase 1 (initial target) |
| Google Chrome | V3 | Phase 4 |
| Firefox | V2 / WebExtension | Phase 4 |
| Safari | Safari Web Extension | Phase 4 |

## Repository layout

```
extension/
  manifest.json         # Manifest V3
  background/           # service worker
    background.js
  content/              # injected into dashboard tab
    content.js
  offscreen/            # keeps MediaRecorder alive across SW suspension
    offscreen.html
    offscreen.js
  popup/                # consent toggle + status
    popup.html
    popup.js
    popup.css
  shared/               # cross-cutting helpers
    events.js
    redaction.js
    storage.js
dashboard-server/       # optional: routes to add to ai-dashboard server.py
  extension_routes.md
```

## Consent & privacy (hard gates)

1. Consent is required before any recording starts. Default off.
2. Recording is scoped to the dashboard tab only.
3. A visible badge/icon shows while recording.
4. Password/secret/credential fields are redacted from event payloads.
5. Recordings and events are stored locally under `~/.dashboard-extension/`.
6. Every start/stop is written to a consent audit log.
7. Recordings carry a TTL; the user can delete any recording at any time.

## Development

```bash
# Load the unpacked extension in Edge:
# edge://extensions/ -> Developer mode -> Load unpacked -> select extension/

# Run the dashboard (separate repo):
# cd ~/Projects/ai-dashboard && python3 server.py   # :7878
```
