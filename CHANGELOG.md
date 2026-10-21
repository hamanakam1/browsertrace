# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Nothing yet

### Changed
- Nothing yet

### Deprecated
- Nothing yet

### Removed
- Nothing yet

### Fixed
- Nothing yet

### Security
- Nothing yet

## [1.4.0] - 2026-10-21

### Added
- Persistent status window opened by explicit On, with a toolbar control to reopen or focus it.
- Separate runtime/activity labels, bounded status freshness, event and ticket counts, and visible retained-history and delivery-receipt boundaries.
- Independent Off controls available during capture, generation, delivery, status waits, and startup.

### Security
- Opening, closing, polling, or restoring the status window does not start capture or the backend. No new browser permissions or startup service.
- Status-window messages cannot grant capture consent, select a page, start recording, or retry abandoned work.

## [1.3.0] - 2026-10-21

### Added
- Explicit browser On / Off controls with an on-demand native helper for Chrome and Edge on macOS/Linux.
- Scoped helper installation and uninstall, exact extension-origin registration, and private configuration.
- Lifecycle regression tests and an isolated real-browser verification report in `docs/BROWSER_CONTROL_TEST_RESULTS.md`.

### Changed
- Off preserves captured evidence, clears capture consent, cancels unfinished automatic ticket delivery, and stops only the browser-owned backend.
- Reopening the popup or browser does not start Panther; turning On again does not resume recording or abandoned delivery.
- Normal Stop & deliver keeps Panther on while the opted-in ticket workflow finishes. Panther creates tickets but does not execute their work.

### Fixed
- Shutdown proceeds even while browser storage is stalled, without hiding persistence uncertainty.
- Ordinary status polling detects a dead owned backend without reconnecting automatically.
- Final delivery checks prevent a new destination request when authority is revoked after receipt persistence.

### Security
- Native commands cannot supply executable paths, shell commands, environment variables, or alternate destinations.
- No login service, HTTP shutdown endpoint, automatic reconnect, or adoption of another process was added.

## [1.0.0] - 2026-10-21

### Added
- Initial release
- Manifest V3 browser extension for Chrome, Edge, Firefox, Safari
- Local-first Python backend on port 7890
- Opt-in consent-gated recording
- Page interaction tracing with secret redaction
- Local Ollama inference
- Intent decomposition into actionable tickets
- Screenshot capture at decision points
- Audio capture support
- Recordings library with video playback
- 30-day TTL and automatic cleanup
- HTTP Range support for video seeking
