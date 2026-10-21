# panther-intent-capture

```
               ___
              /   \
             /  _  \
            | (o o) |
            |   ^   |
             \  -  /
            /   |   \
           /    |    \
          /     |     \
         |      |      |
         |      |      |
        /       |       \
       /        |        \
      |   \     |     /   |
      |    \    |    /    |
       \    \   |   /    /
        \    \  |  /    /
         \    \ | /    /
          \____\|____/
             |    |
             |    |
            /      \
           /        \
          |          |
          |          |
           \        /
            \______/
   ___          ___
  /   \        /   \
 |     |      |     |
 |     |      |     |
  \   /        \   /
   \_/          \_/

   ┌─────────────┐
   │ ┌─────────┐ │
   │ │         │ │
   │ │         │ │
   │ └─────────┘ │
   └─────────────┘
```

> Decompose intent on a website or portal. Turn that intelligence into tickets, with screenshots, audio, and video — so coding agents can take action based on what was captured.

**panther-intent-capture** is a Manifest V3 browser extension plus a local inference backend. It records interactions on pages you configure, infers the user's intent from those interactions, and emits structured work items (tickets) that coding agents can act on. Everything runs locally, nothing leaves your machine unless you export it, and recording is opt-in by default.

Built first for **Microsoft Edge**, with **Chrome** and **Firefox** variants ready.

---

## Purpose

panther-intent-capture captures what a person actually does on a company dashboard, portal, or website — clicks, navigation, form edits, and the pages they touch — decomposes that behavior into intent (e.g. "approve expense report", "reconcile vendor invoice", "add new employee"), and converts each intent into an actionable ticket:

- **Intent decomposition** — observed interactions are grouped and labeled by goal
- **Tickets** — each intent becomes a work item with description, steps to reproduce, and owner
- **Screenshots** — key frames captured at decision points and attached to the ticket
- **Audio / video recording** — session media captured for later review (planned)
- **Agent-ready output** — tickets and media are structured so coding agents can read the intent and take action (create issues, write code, run tests, deploy fixes)

---

## Brand

| | |
|---|---|
| **Name** | panther-intent-capture |
| **Tagline** | Capture Intent. Understand Behavior. |
| **Scope** | Browser extension + standalone local inference backend |
| **Initial target** | Microsoft Edge (Manifest V3) |
| **Also ready** | Google Chrome, Firefox |

### Visual identity

Brand colors:

```
#0a0a0a  panther-black   primary
#1a1a1a  panther-dark    surfaces
#06b6d4  intent-cyan     trace / capture
#10b981  emerald-500     consent / ok
#ef4444  rose-500        stop / delete
#fbbf24  amber-400       panther accent
```

The mark is a stalking panther framing an **eye** (observe) and a **cursor trail** (intent captured).

---

## What it does

1. **Opt-in recording** — Consent is required before anything is captured. Default is off.
2. **Page-scoped capture** — Records only pages you configure in the manifest. Never arbitrary sites, never full desktop by default.
3. **Interaction tracing** — Captures click / keydown / change / submit events with target, coordinates, and timestamp.
4. **Secret redaction** — Password / token / credential-named fields are scrubbed before storage (extension + backend).
5. **Local inference** — Sampled frames are sent to a local Ollama instance; a narrative analysis of the session's intent is generated.
6. **Intent decomposition** — The interaction timeline is decomposed into named intents and converted into tickets.
7. **Tickets with screenshots** — Each ticket carries description, steps, and attached screenshots from the session.
8. **Recordings library** — Play video, view inference results, delete recordings. 30-day TTL + cap.
9. **Audio / video** — Session media captured for later review by coding agents.

---

## Architecture

```
 _______________________________________________________________________________
|                                                                               |
|  BROWSER EXTENSION (Manifest V3)                         STANDALONE BACKEND   |
|                                                                               |
|  ┌─────────┐   ┌──────────────┐   ┌─────────────────────┐      ┌────────┐   |
|  │  Popup  │──▶│ Content script│──▶│ Offscreen (MediaRecorder)│──▶ POST │   |
|  │ consent │   │ page events   │   │ video + events bundle      multipart│   |
|  └─────────┘   └──────────────┘   └─────────────────────┘      └────┬────┘   |
|                                                                               │   ▼
|                                   ┌─────────────────────────────────────────┐   |
|                                   │  /api/record   /api/list   /api/infer   │   |
|                                   │  /video          /delete   /health        │   |
|                                   │        (Python, :7890)                   │   |
|                                   └────────────────┬────────────────────────┘   |
|                                                    │ local only                  │   |
|                                                    ▼                             │   |
|                                   ┌─────────────────────────────────────────┐   |
|                                   │  ~/.panther-intent-capture/                         │   |
|                                   │  recordings/ events/ inference/ consent   │   |
|                                   └─────────────────────────────────────────┘   |
|_______________________________________________________________________________|

Recorded session
        │
        ▼
┌──────────────────┐   decompose intent   ┌──────────────────┐
│  raw events +    │─────────────────────▶│  intent tickets  │
│  screenshots     │                      │  (work items)    │
└──────────────────┘                      └─────────┬────────┘
                                                   │ coding agent
                                                   ▼
                                          action: issue / code / test / deploy
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
cp backend/launchd/com.panther-intent-capture.backend.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.panther-intent-capture.backend.plist
```

### 2. Load the extension

**Microsoft Edge:**
1. `edge://extensions/` → **Developer mode** → **Load unpacked**
2. Select `extension/`

**Google Chrome:** use `extension/manifest.chrome.json`  
**Firefox:** use `extension/manifest.firefox.json`

### 3. Record

1. Open any page you've added to `host_permissions` in the manifest
2. Click the extension icon → enable consent → **Start Recording**
3. Browse and interact normally
4. Click **Stop Recording**
5. Open **Extension options** → view video + inference results + generated tickets

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
| **Page-scoped** | `tabCapture` limited to pages you configure; no arbitrary sites |
| **Visible indicator** | Persistent badge while recording; browser tab indicator enforced |
| **No secrets** | Password/token/credential fields redacted before storage |
| **Local-first** | Everything stays under `~/.panther-intent-capture/` unless you export |
| **User deletes** | 30-day TTL; any recording can be deleted at any time |
| **Consent audit** | Every start/stop logged with timestamp |

---

## Contributing

panther-intent-capture is open source and we welcome contributions. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to get started, and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for our community standards.

### Good first issues
- Add a Safari Web Extension variant
- Add real-time / streaming inference overlay
- Add click heatmap visualization
- Add encrypted export for sharing recordings
- Add audio capture alongside video

### Security
See [`SECURITY.md`](SECURITY.md) for vulnerability reporting.

---

## License

[MIT](LICENSE)

---

Made for decomposing intent on dashboards and portals — transparently, locally, and with consent.
