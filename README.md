# browsertrace

![browsertrace logo](assets/logo/browsertrace.svg)

> Observe dashboard usage, trace interactions, infer insights — all locally, all with consent.

**[browsertrace](https://github.com/hamanakam1/browsertrace)** is a Manifest V3 browser extension that records video of dashboard usage and click/interactions with explicit user consent, then runs local inference to surface how a dashboard is actually being used.

Built first for **Microsoft Edge**, with **Chrome** and **Firefox** variants ready. Storage and inference stay on the operator's machine — nothing leaves unless you choose to export.

---

## Brand

| | |
|---|---|
| **Name** | browsertrace |
| **Tagline** | Observe. Trace. Understand. |
| **Scope** | Browser extension + standalone local inference backend |
| **Initial target** | Microsoft Edge (Manifest V3) |
| **Also ready** | Google Chrome, Firefox |

### Visual identity

Brand colors:

```
#6366f1  indigo-500   primary
#06b6d4  cyan-500     trace
#10b981  emerald-500  consent / ok
#ef4444  rose-500     stop / delete
```

The mark is a single continuous line forming both an **eye** (observe) and a **cursor trail** (trace).

---

## What it does

1. **Opt-in recording** — Consent is required before anything is captured. Default is off.
2. **Dashboard-scoped capture** — Records only the dashboard tab via `tabCapture`. Never arbitrary sites, never full desktop by default.
3. **Interaction tracing** — Captures click / keydown / change / submit events with target, coordinates, and timestamp.
4. **Secret redaction** — Password / token / credential-named fields are scrubbed before storage (extension + backend).
5. **Local inference** — Sampled frames are sent to a local Ollama instance; a narrative analysis of the session is generated.
6. **Recordings library** — Play video, view inference results, delete recordings. 30-day TTL + cap.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser Extension (Manifest V3)                            │
│  ┌─────────┐  ┌──────────────┐  ┌─────────────────────┐    │
│  │ Popup   │  │ Content script│  │ Offscreen (MediaRecorder)│ │
│  │ consent │──▶ click events  │──▶ video + events bundle   │ │
│  └─────────┘  └──────────────┘  └─────────────────────┘    │
└───────────────────────────────┬─────────────────────────────┘
                                │ POST multipart
                                ▼
┌─────────────────────────────────────────────────────────────┐
│  Standalone Backend (Python, :7890)                         │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │ /api/record │──▶ storage     │──▶ /api/infer       │   │
│  │ /list       │    (filesystem)│    (Ollama chat)    │   │
│  │ /video      │    + redaction │    + narrative      │   │
│  └─────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                                │ local only
                                ▼
┌─────────────────────────────────────────────────────────────┐
│  ~/.browsertrace/  (recordings, events, inference, consent) │
└─────────────────────────────────────────────────────────────┘
```

---

## Quick start

### 1. Start the backend

```bash
cd backend
./start.sh          # listens on http://127.0.0.1:7890
```

Or install as a launchd service:

```bash
cp backend/launchd/com.michaelhamanaka.browsertrace-backend.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.michaelhamanaka.browsertrace-backend.plist
```

### 2. Load the extension

**Microsoft Edge:**
1. `edge://extensions/` → **Developer mode** → **Load unpacked**
2. Select `extension/`

**Google Chrome:** use `extension/manifest.chrome.json`  
**Firefox:** use `extension/manifest.firefox.json`

### 3. Record

1. Open `http://127.0.0.1:7878` (your dashboard)
2. Click the extension icon → enable consent → **Start Recording**
3. Do some dashboard interactions
4. Click **Stop Recording**
5. Open **Extension options** → view video + inference results

### 4. Tests

```bash
# Extension redaction unit test (Deno)
deno test tests/redaction_test.mjs
```

---

## Privacy & consent (hard gates)

| Rule | Enforcement |
|---|---|
| **Opt-in only** | Recording is off by default; explicit consent per session |
| **Dashboard-scoped** | `tabCapture` limited to the dashboard tab; no arbitrary sites |
| **Visible indicator** | Persistent badge while recording; browser tab indicator enforced |
| **No secrets** | Password/token/credential fields redacted before storage |
| **Local-first** | Everything stays under `~/.browsertrace/` unless you export |
| **User deletes** | 30-day TTL; any recording can be deleted at any time |
| **Consent audit** | Every start/stop logged with timestamp |

---

## Contributing

browsertrace is open source and we welcome contributions. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to get started, and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for our community standards.

### Good first issues
- Add a Safari Web Extension variant
- Add real-time / streaming inference overlay
- Add click heatmap visualization
- Add encrypted export for sharing recordings

### Security
See [`SECURITY.md`](SECURITY.md) for vulnerability reporting.

---

## License

[MIT](LICENSE)

---

Made for observing dashboard usage — transparently, locally, and with consent.
