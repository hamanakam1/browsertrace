# Panther Intent Capture

Turn a browser session into a clear, reviewable request for improvement.

Panther records the interactions you choose to capture, keeps the evidence on your computer, and helps you explain what should change. Review the resulting draft, add the expected outcome, then send it to AI Dashboard or export JSON for another system.

The current implementation targets Chrome and Microsoft Edge 116+ with a Manifest V3 extension and a small Python backend. It works on ordinary `http` and `https` pages that allow extension injection. You do not need to add each website to the manifest. Browser settings pages, extension stores, embedded frames, and native desktop applications are outside the supported capture scope.

## What works in this version

- **Chosen-tab capture:** start from the extension popup; record clicks, changes, submissions, and navigation within that tab's origin. Capture starts off. Typed values, typed keys, and full page text are excluded.
- **Visible controls:** a compact popup, recording badge, and floating page panel show capture status. The panel can stop capture and open the drafts. Hiding the panel does not stop capture.
- **Optional screenshots:** opt in separately for each session to collect up to five screenshots at click or submission points. Screenshot pixels are **not redacted**; inspect them before sharing.
- **Evidence drafts without a model:** stopping saves the capture and prepares a draft from observed actions and your stated goal. This does not infer that a bug exists.
- **Optional local AI:** explicitly choose **Generate with local AI** for proposed intents and task decomposition using your configured local Ollama model. No paid or remote inference fallback is used.
- **Human review:** inspect events and any screenshots, edit the title, problem statement, and acceptance criteria, and save draft edits locally.
- **AI Dashboard delivery:** preview the exact ticket, then confirm one draft at a time. The adapter creates a backlog ticket with `no-auto`, preserves capture identity for retries, and independently reads it back before showing verified delivery.
- **Portable JSON:** export the same draft and provenance for another tool. Additional destinations need an adapter or importer; GitHub, Jira, Linear, and arbitrary native apps are not built-in integrations yet.

Video capture, audio capture, and media playback are disabled in this release. Legacy media files in the repository are not a supported capture path. Firefox and Safari manifests are experimental placeholders, not supported browser builds.

## Quick start

You need Python 3.11+ and Chrome or Edge 116+. The core backend uses Python's standard library; no package installation or Ollama service is needed for evidence drafts.

From a checkout of this repository:

```bash
python3 backend/server.py
```

Keep that terminal running. Open [the local review page](http://127.0.0.1:7890/) to check the backend and browse captures. `bash backend/start.sh` starts the same server. Stop it with Ctrl-C. Automatic launchd installation is not part of this quick start.

Load the extension:

1. Open `edge://extensions/` or `chrome://extensions/`.
2. Enable **Developer mode**, choose **Load unpacked**, and select this repository's `extension/` directory.
3. Pin Panther in the browser toolbar. The default `extension/manifest.json` works for both supported browsers; no manifest swapping is needed.
4. Open the website you want to improve, such as AI Dashboard at `http://127.0.0.1:7878/`.
5. Open Panther, enable capture consent, and write a short goal in **What should improve?** Keep screenshots off unless you want to retain visible page pixels.
6. Click **Start capture**, reproduce the workflow, then choose **Stop & generate drafts** in the popup or page panel.
7. Click **Review drafts**. Check the observed steps, describe the problem or improvement, and add a verifiable expected result.
8. Choose **Save draft edits** before exporting JSON, or **Preview AI Dashboard ticket** to inspect the exact outbound payload. **Send this reviewed ticket** performs the dashboard write.

Capture stops when you leave the chosen origin, close the tab, revoke capture consent, or reach the event limit. Same-origin reloads and SPA navigation are supported. A browser restart leaves an interrupted capture available for recovery rather than resuming recording automatically.

## AI Dashboard and other systems

AI Dashboard is the first destination adapter. By default Panther's backend connects to `http://127.0.0.1:7878`; the extension never writes directly to the dashboard. The dashboard must already be running and provide the supported MasterBoard API. Panther does not install, restart, or change dashboard services.

Submitted tickets contain a bounded summary of intent, observed evidence, reproduction steps, acceptance criteria, source URL, timestamps, and provenance. Media references are not uploaded screenshot attachments. Sending a draft grants no permission to execute it: tickets remain in backlog with the `no-auto` review fence.

### Intent bundle

For another system, use **Export JSON** and map the versioned intent bundle into that system's own draft or issue format. The capture and review workflow is independent of the destination. See [integration contracts and adapter guidance](docs/INTEGRATIONS.md) for the export shape, HTTP routes, retry behavior, and configuration.

## Local AI and configuration

All settings below are backend environment variables. Set them before starting the server.

| Variable | Default | Purpose |
|---|---|---|
| `PANTHER_DATA_DIR` | `~/.panther-intent-capture` | Private capture, draft, and receipt storage. |
| `PANTHER_DASHBOARD_URL` | `http://127.0.0.1:7878` | AI Dashboard's loopback HTTP(S) origin. Remote addresses and redirects are rejected. |
| `OLLAMA_ENDPOINT` | `http://127.0.0.1:11434/v1` | Local OpenAI-compatible inference endpoint. A loopback HTTP address is required. |
| `OLLAMA_MODEL` | `qwen3.8-hermes:latest` | Model name used only when local AI generation is explicitly requested. It must already be available on your computer. |
| `EXTENSION_BACKEND_PORT` | `7890` | Backend listener port. The shipped extension expects `7890`; changing this also requires updating its backend origin and host permission. |

For example, choose an already installed local model when starting the backend:

```bash
OLLAMA_MODEL=your-installed-model python3 backend/server.py
```

An unavailable model or invalid model response produces a visible generation error and keeps the saved capture available. You can retry or use the evidence draft. Current event-only capture does not promise visual analysis of the optional screenshots; provenance records whether media was actually analyzed.

## Privacy, retention, and recovery

Panther captures a small allowlist of control metadata instead of input values or keystrokes. URL sanitization removes credentials and query parameters; text sanitization removes common secret patterns. These filters reduce exposure but do not guarantee that every identifier, control label, user-written goal, or screenshot is free of private information.

Screenshots require separate consent and may include any visible content in the chosen tab. They are stored locally for review. JSON exports and dashboard summaries contain evidence references rather than image data.

While recording, the browser persists capture state so the extension service worker can restart. If the backend is unavailable at Stop, Panther shows **Upload pending** and keeps the capture in browser storage. Use **Retry upload** after starting the backend; the same session identity prevents a second recording. **Discard unsaved capture** removes that browser copy. Generation failure has a separate retry because the recording may already be saved.

Backend captures have a 30-day retention limit and a cap of 200 records, enforced when storage is accessed. **Delete capture** removes its local capture artifacts and saved drafts. It does not remove downloaded exports, browser consent history, already submitted dashboard tickets, or delivery receipts. Receipts retain capture identity, a content hash, target, ticket ID, and delivery state to prevent duplicate submissions; they do not retain the full captured evidence.

The backend binds to `127.0.0.1`. Its origin and request-header checks are browser request protections, not user authentication. Treat this as a local single-user tool; do not expose the port publicly or use the header as an access token.

## Development and verification

Run the focused synthetic test suites from the repository root:

```bash
node --test tests/*.mjs
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

The Python tests use temporary data directories and fake loopback dashboard/inference servers. The JavaScript tests exercise capture state and redaction with browser API doubles. Passing them does not establish that the unpacked extension has been installed and exercised in a real browser, that a particular local AI model works, or that a live AI Dashboard ticket has been accepted. Those are separate checks using a harmless, consented capture and explicit delivery confirmation.

See the [1.1.0 test results and pilot limits](docs/TEST_RESULTS.md) for the automated and real-browser evidence. Build an unpackable Chromium ZIP with `python3 scripts/package_extension.py`; CI also retains it as an artifact.

See [ROADMAP.md](ROADMAP.md) for remaining work, [CONTRIBUTING.md](CONTRIBUTING.md) for contributing, and [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

[MIT](LICENSE)
