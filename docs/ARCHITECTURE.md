# Panther Intent Capture - Architecture

## Overview

Panther Intent Capture is a local-first browser extension + Python backend system that captures user intent on web pages and converts it into actionable tickets for AI coding agents.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Browser (Chrome/Edge)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  Extension (Manifest V3)                                                     │
│  ├── Popup (popup/)        - User controls, consent, automation settings    │
│  ├── Content Script        - DOM event capture, redaction, page panel       │
│  ├── Background SW         - Orchestration, screenshot capture, messaging   │
│  ├── Offscreen Document    - MediaRecorder for future video support          │
│  └── Options Page          - Review drafts, view captures, manage settings  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │ HTTP POST (multipart)
                                    │ 127.0.0.1:7890
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Python Backend (Standard Library)                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  ├── server.py             - HTTP server, routes, static files             │
│  ├── api/routes.py         - API endpoints                                  │
│  ├── storage.py            - Local storage (~/.panther-intent-capture/)    │
│  ├── automation.py         - Durable job queue for auto-delivery            │
│  ├── integrations.py       - AI Dashboard adapter                           │
│  ├── inference.py          - Ollama integration for AI generation           │
│  ├── redaction.py          - Secret/credential redaction                    │
│  └── tickets.py            - Intent decomposition to tickets                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │ HTTP
                                    │ 127.0.0.1:7878
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            AI Dashboard (External)                          │
│                         MasterBoard API for ticket creation                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### Manual Workflow
1. **Consent**: User enables capture consent in popup
2. **Capture**: Content script captures DOM events (clicks, changes, submits, navigation)
3. **Stop**: User stops capture → events sent to backend
4. **Draft Generation**: Backend creates draft from events + goal
5. **Review**: User reviews/edits draft in options page
6. **Delivery**: User previews and sends to AI Dashboard (with confirmation)
7. **Verification**: Backend reads back ticket to verify creation

### Automated Workflow
1. **Session Setup**: User enables automation + destination before starting
2. **Capture**: Same as manual
3. **Stop**: Extension signals stop to backend
4. **Auto-Process**: Backend saves capture, generates proposals, delivers to dashboard, verifies
5. **Status Updates**: Extension polls job status, shows progress
6. **Completion**: User sees verified ticket count

## Privacy & Security

### Privacy Principles
- **Local-first**: All data stored in `~/.panther-intent-capture/`
- **Opt-in only**: Recording off by default, explicit consent required
- **Page-scoped**: Only chosen tab/origin, no full desktop capture
- **No telemetry**: No analytics, no external reporting
- **User control**: Can delete any capture, 30-day TTL

### Security Measures
- **Redaction**: Password/token/credential fields scrubbed
- **Local-only binding**: Backend binds to 127.0.0.1 only
- **Content security**: CSP headers, no eval
- **Provenance tracking**: All tickets include capture identity
- **Review fence**: Dashboard tickets created with `no-auto` tag
- **No secrets in exports**: Exports contain evidence references, not credentials

## Storage Layout

```
~/.panther-intent-capture/
├── captures/           # Raw capture events
├── drafts/             # Generated draft tickets
├── automation/         # Durable job state
├── receipts/           # Delivery receipts
└── logs/               # Backend logs
```

## Extension Architecture

### Manifest V3 Components
- **Service Worker (background)**: Central coordinator, handles messaging
- **Content Script**: Runs on pages, captures events, shows panel
- **Popup**: Extension popup UI
- **Options Page**: Full review interface
- **Offscreen Document**: For MediaRecorder (future use)

### Permissions
- `storage` - Local settings
- `tabs` - Active tab access
- `scripting` - Content script injection
- `alarms` - Automation job scheduling

### Host Permissions
- `http://127.0.0.1:7890/*` - Backend API
- All URLs for content script injection (user can restrict)

## Backend API

Key endpoints:
- `POST /api/capture/start` - Start capture session
- `POST /api/capture/stop` - Stop and process capture
- `GET /api/drafts` - List drafts
- `POST /api/drafts/:id/deliver` - Deliver to dashboard
- `POST /api/automation/jobs` - Create automation job
- `GET /api/automation/jobs/:id` - Job status

See [INTEGRATIONS.md](docs/INTEGRATIONS.md) for full API documentation.

## Technology Stack

- **Extension**: Vanilla JavaScript (ES modules), HTML5, CSS3
- **Backend**: Python 3.11+ standard library only (no dependencies)
- **Storage**: JSON files on disk
- **AI**: Ollama (local, optional)
- **Testing**: Node.js test runner, Python unittest

## Future Considerations

- Firefox and Safari support (currently placeholders)
- Video/audio capture re-enablement
- Additional dashboard integrations
- Plugin system for custom adapters
- Enhanced redaction rules
- Browser sync (explicitly not planned for privacy)
