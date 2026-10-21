# Browser-controlled On / Off

Panther is off until you explicitly choose **Turn Panther on** in the extension popup. There is no login item, launchd/systemd service, scheduled startup, or automatic reconnect. Opening the popup, checking status, receiving an alarm, or restarting the browser does not turn it on.

The browser starts a narrowly scoped local helper through [Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging), also [supported by Edge](https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging). This requires a one-time local registration separate from loading the extension. An extension alone cannot launch an arbitrary local Python process.

## One-time setup: macOS or Linux

1. Load this checkout's `extension/` directory in Chrome or Edge, or reload an existing unpacked installation. Version 1.3.0 adds the `nativeMessaging` permission so the popup can control Panther's own helper. Browser permission prompts remain yours to approve.
2. Copy Panther's **extension ID** from `edge://extensions` or `chrome://extensions`. It is 32 lowercase letters. Unpacked extension IDs depend on the installation, so do not copy someone else's ID.
3. From the repository root, register that exact ID for your browser:

```bash
python3 scripts/install_browser_helper.py --browser edge --extension-id YOUR_EXTENSION_ID
```

Use `--browser chrome` for Google Chrome or `--browser chromium` for Chromium. The installer records the current Python interpreter and checkout path; both must remain available. It does not download Python, install a model, start the backend, or install an always-running service. If Python or the checkout moves, uninstall the old registration and register again.

For a custom browser user-data directory, add `--user-data-dir /absolute/path/to/user-data`. This means the directory containing `Default`/profile directories, not a `Default` directory itself. The installer supports macOS/Linux path mappings; Windows helper packaging is not implemented.

Optional setup parameters:

| Parameter | Purpose |
|---|---|
| `--data-dir /absolute/private/path` | Saved captures, jobs, receipts, and helper configuration. Default: `~/.panther-intent-capture`. |
| `--dashboard-url http://127.0.0.1:8080` | Pin a compatible local ticket-service origin; loopback only. This is an example. No destination is configured by default. |
| `--ollama-endpoint http://127.0.0.1:11434/v1` | Optional local inference endpoint; HTTP loopback only. |
| `--ollama-model MODEL_NAME` | Optional already-installed local model; no download or paid fallback. |

The native host is named `com.panther.intent_capture`. Its manifest allows only the exact extension origin you supplied. Browser messages can request `start`, `status`, or `stop`; they cannot provide shell commands, executable paths, environment variables, or alternate destinations. The helper configuration and launcher are private to the current user. Registration refuses to replace a different or edited existing host registration.

Without `--dashboard-url`, capture, draft review, and JSON export work, but ticket delivery is unconfigured. Existing registrations retain their explicitly stored origin. To change it, follow the supported uninstall/reinstall procedure; do not overwrite the private configuration or receipt.

## Everyday controls

- **Turn Panther on** starts only Panther's local backend. It does not start a ticket service or Ollama and does not begin recording. Confirm capture consent and choose **Start capture** separately.
- **Stop & deliver tickets** ends a session and performs the explicitly opted-in capture-to-ticket workflow. Panther remains on so generation, delivery, and verification can finish.
- **Turn Panther off** immediately stops accepting capture events and automatic browser upload retries, preserves an interrupted capture locally, cancels remaining automatic delivery, and shuts down the backend owned by that browser connection. A request already in flight can finish; already created tickets remain.
- Quitting the browser process or losing the helper connection stops the browser-owned backend. Closing only a window may leave the browser running in background, so use **Turn Panther off** for an explicit shutdown. There is no automatic reconnect. The next explicit On cancels stale unfinished automatic jobs before starting the worker; it does not silently resume abandoned delivery.

Turning Panther on again does not resume recording. It requires fresh capture consent. Retained interrupted captures remain visible for explicit recovery; turning the application off is not the same as **Stop & deliver**. Historical delivery receipts and saved evidence are retained. Cancelled automatic jobs are not readmitted by toggling On.

## Failure states

**Helper unavailable / setup needed:** verify the exact extension ID, correct browser registration, executable Python path, and checkout location. Reload Panther after an update, then try On. Reading status does not attempt to install or start anything.

**Port already in use:** another process is listening on `127.0.0.1:7890`. Panther refuses to adopt or stop that process. Stop your manually launched Panther backend yourself before using browser control. Do not kill an unknown listener.

**Stopping / shutdown not confirmed:** the UI must not claim Off while shutdown is uncertain. Capture and further local sends are fenced immediately; an in-flight destination write cannot be retracted. Check the displayed error rather than repeatedly toggling On.

The loopback API is still a single-user local API, not authenticated multi-user infrastructure. Native messaging does not make it suitable for public exposure. Do not expose port 7890.

## Uninstall the helper

Turn Panther off first, then run the same registration command with `--uninstall`:

```bash
python3 scripts/install_browser_helper.py --browser edge --extension-id YOUR_EXTENSION_ID --uninstall
```

Repeat any custom `--data-dir` and `--user-data-dir` arguments. Uninstall removes only the unchanged helper registration, launcher, configuration, and installation receipt. Captures, delivery receipts, the checkout, and unrelated browser configuration remain. Changed files are not removed automatically. Re-running installation restores browser control.

## Developer-only manual server

`python3 backend/server.py` remains available for backend development and the local HTTP review UI. The extension does not treat a listening manual server as browser-On authority and does not shut it down. Normal browser capture uses the on-demand helper above.
