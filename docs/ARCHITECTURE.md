# Panther Intent Capture - Architecture

## Overview

Panther Intent Capture is a local-first browser extension + Python backend system that captures user intent on web pages and converts it into reviewable tickets. It does not implement or dispatch their work.

## System Architecture

The component sketch below preserves the earlier high-level overview, not the complete 1.3.0 runtime lifecycle. Browser On now launches an owned backend through a separately registered native helper; Off stops it and cancels unfinished delivery. See [browser control](BROWSER_CONTROL.md) for the current lifecycle. The offscreen/media component is legacy code, not an enabled capture path.

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
│  ├── integrations.py       - Optional local ticket-service adapter         │
│  ├── inference.py          - Ollama integration for AI generation           │
│  ├── redaction.py          - Secret/credential redaction                    │
│  └── tickets.py            - Intent decomposition to tickets                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │ HTTP
                                    │ Explicitly configured loopback origin
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Compatible local ticket service                    │
│                         Optional; no default destination                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

Both browser workflows require explicit **Turn Panther on** first. Turning On does not grant capture consent or start recording. Normal Stop keeps Panther on; Off preserves interrupted evidence and cancels remaining automatic delivery.

### Manual Workflow
1. **Consent**: User enables capture consent in popup
2. **Capture**: Content script captures DOM events (clicks, changes, submits, navigation)
3. **Stop**: User stops capture → events sent to backend
4. **Draft Generation**: Backend creates draft from events + goal
5. **Review**: User reviews/edits draft in options page
6. **Delivery**: User previews and sends to local ticket service (with confirmation)
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
- **Local-first**: Backend data defaults to `~/.panther-intent-capture/`; interrupted captures and settings also use local browser storage. The data directory is configurable.
- **Opt-in only**: Recording off by default, explicit consent required
- **Page-scoped**: Only chosen tab/origin, no full desktop capture
- **No telemetry**: No analytics, no external reporting
- **User control**: Can delete any capture, 30-day TTL

### Security Measures
- **Redaction**: Typed values and keys are excluded; common-secret and URL sanitization is best-effort. Optional screenshot pixels are not redacted.
- **Local-only binding**: Backend binds to 127.0.0.1 only
- **Content security**: CSP headers, no eval
- **Provenance tracking**: All tickets include capture identity
- **Review fence**: Dashboard tickets created with `no-auto` tag
- **Bounded exports**: Exports exclude raw event arrays, screenshot image data, local paths, and destination credentials. Review included draft text and metadata before sharing; sanitization is not a guarantee that all sensitive content is removed.

## Storage Layout

Under the configured data root, `manifest.json` indexes captures; `recordings/` and `events/` retain capture artifacts, `inference/` holds inference results, and `tickets/` holds saved drafts. `automation/` stores jobs, `automation-cancelled/` stores cancellation markers, and `deliveries/` stores delivery receipts. Browser-helper configuration is separate under `browser-helper/`. Consent history uses `consent.log`; browser-owned shutdown records a content-free `browser-revocation.json` marker.

## Extension Architecture

### Manifest V3 Components
- **Service Worker (background)**: Central coordinator, handles messaging
- **Content Script**: Runs on pages, captures events, shows panel
- **Popup**: Extension popup UI
- **Options Page**: Full review interface
- **Offscreen Document**: For MediaRecorder (future use)

### Permissions
- `storage` - Local settings
- `activeTab` - Temporary chosen-tab access after the user's extension interaction
- `scripting` - Content script injection
- `alarms` - Automation job scheduling
- `nativeMessaging` - Explicit On / Off through the registered local helper

### Host Permissions
- `http://127.0.0.1:7890/*` - Backend API
- No all-URLs host grant; chosen-page injection uses `activeTab` and `scripting`. Restricted browser pages remain unsupported.

## Backend API

Capture start/stop is controlled inside the extension, not through HTTP start/stop endpoints. Key backend routes, all prefixed by `/api/extension`, are:

- `POST /record` - Save a stopped capture and admit an explicitly opted-in automatic policy
- `GET /recordings` - List saved captures
- `GET /tickets/:id` and `POST /tickets/:id` - Read or generate drafts for a capture
- `POST /tickets/:id/review` - Save reviewed draft edits
- `POST /integrations/ticket-service/preview/:id` and `POST /integrations/ticket-service/submit/:id` - Preview and confirm manual ticket delivery
- `GET /automation/:id` and `POST /automation/:id` - Read job status or request a supported control action
- `GET /export/:id` - Export the portable intent bundle

See [integration contracts](INTEGRATIONS.md), [automation behavior](AUTOMATION.md), and the current route implementation in `backend/api/routes.py` for request and authority requirements.

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
