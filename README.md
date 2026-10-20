# browsertrace

Standalone browser extension (Manifest V3) that observes dashboard usage and acts on that intelligence. Built first for Microsoft Edge, with Chrome, Firefox, and Safari targets planned.

## What it does

1. **Opt-in recording** — user enables consent in the extension popup, then starts recording the dashboard tab.
2. **Video + interaction capture** — `tabCapture` records the dashboard tab video; a content script captures click/keydown/change/submit interactions.
3. **Secret redaction** — password/token/credential-named fields are scrubbed before anything is stored (both in the extension and the backend).
4. **Local inference** — recordings are sent to a standalone Python backend that samples frames and runs Ollama (`http://127.0.0.1:11434/v1`) to produce a narrative analysis of the session.
5. **Recordings library** — the extension options page lists recordings, shows inference results, and lets you delete recordings.

## Architecture

```
Edge extension (Manifest V3)
  ├── content script: click/interaction capture → background service worker
  ├── offscreen doc: tabCapture video → MediaRecorder → WebM chunks
  ├── popup: consent toggle, start/stop, status
  ├── options: recordings library, inference results, delete
  └── background: orchestrates capture, POSTs bundles to backend

Standalone backend (Python, :7890)
  ├── POST   /api/extension/record        — receive video + events bundle
  ├── GET    /api/extension/recordings    — list recordings
  ├── GET    /api/extension/recordings/:id — recording details
  ├── GET    /api/extension/recordings/:id/video — video (with Range support)
  ├── DELETE /api/extension/recordings/:id
  ├── POST   /api/extension/infer/:id     — run Ollama inference
  └── GET    /api/extension/infer/:id     — get inference result

Storage: `~/.browsertrace/{recordings,events,inference,consent.log,manifest.json}` (outside the repo, owner-only permissions).

## Browser targets

| Browser | Manifest | Status |
|---|---|---|
| Microsoft Edge | V3 | ✅ Ships (manifest.json) |
| Google Chrome | V3 | ✅ Ready (manifest.chrome.json) |
| Firefox | V3 | ✅ Ready (manifest.firefox.json) |
| Safari | Safari Web Extension | Planned |

## Running

```bash
# 1. Start the standalone backend (one command)
cd ~/Projects/dashboard-extension/backend
./start.sh              # or: python3 server.py   # :7890

# 2. (Optional) Install as a launchd service
cp backend/launchd/com.michaelhamanaka.browsertrace-backend.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.michaelhamanaka.browsertrace-backend.plist

# 3. Load the unpacked extension in Edge
# edge://extensions/ → Developer mode → Load unpacked → select extension/

# 4. Record: click extension icon on http://127.0.0.1:7878 → enable consent → Start Recording
# 5. View: Extension options → Recordings library → play video + inference
## Repository layout

```
extension/
  manifest.json         # Manifest V3
  background/           # service worker
    background.js
  content/              # injected into dashboard tab
    content.js
    content.css
  offscreen/            # keeps MediaRecorder alive across SW suspension
    offscreen.html
    offscreen.js
  popup/                # consent toggle + status
    popup.html
    popup.js
    popup.css
  options/              # recordings library + inference results
    options.html
    options.js
    options.css
  shared/               # cross-cutting helpers
    constants.js
    audit.js
    redaction.js
    storage.js
  icons/
backend/
  server.py             # standalone HTTP server (:7890)
  api/routes.py         # record/list/get/delete/infer routes
  inference.py          # frame sampling + Ollama chat
  redaction.py          # secret redaction (defense in depth)
  storage.py            # file-backed recording store
tests/
  redaction_test.mjs    # unit test
```

## Consent & privacy (hard gates)

1. Consent is required before any recording starts. Default off.
2. Recording is scoped to the dashboard tab only (`http://127.0.0.1:7878/*`).
3. A visible badge/icon shows while recording; the browser enforces a tab indicator.
4. Password/secret/credential-named fields are redacted before storage.
5. Recordings and inference stay on the operator's machine under `~/.browsertrace/`.
6. Every start/stop is written to a consent audit log.
7. Recordings carry a 30-day TTL; the user can delete any recording at any time.

## Development

### 1. Start the standalone backend

```bash
cd ~/Projects/dashboard-extension/backend
python3 server.py          # listens on http://127.0.0.1:7890
```

### 2. Load the unpacked extension in Edge

```
edge://extensions/ → Developer mode → Load unpacked → select ~/Projects/dashboard-extension/extension
```

### 3. Record

- Click the extension icon while on `http://127.0.0.1:7878`
- Check the consent box, then click **Start Recording**
- Do some dashboard interactions, then click **Stop Recording**
- Open **Extension options** (from `edge://extensions/` → Details → Extension options) to view recordings and inference results

### 4. Run the dashboard (separate repo)

```bash
cd ~/Projects/ai-dashboard && python3 server.py   # :7878
```

### Tests

```bash
# Extension redaction unit test (Deno)
deno test tests/redaction_test.mjs
```

## Roadmap

See `ROADMAP.md` for Phase 4 (cross-browser) and Phase 5 (real-time inference, heatmaps, encrypted export, etc.).
