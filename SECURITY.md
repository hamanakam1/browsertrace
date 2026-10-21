# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.2.x   | Yes       |
| 1.0.x–1.1.x | Upgrade to 1.2.x |

## Reporting a Vulnerability

panther-intent-capture deals with sensitive user data (screen recordings, interaction traces). We take security seriously.

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities.
2. Email the maintainer with details of the vulnerability, steps to reproduce, and any suggested fix.
3. Allow reasonable time for a response and fix before public disclosure.

## Security Principles

panther-intent-capture is designed around these security boundaries:

* **Opt-in capture** — recording never starts without explicit user consent
* **Targeted capture** — explicit Start grants access to the chosen tab; capture stops when it leaves the chosen origin
* **Data minimization** — input values, typed keys, and general page text are excluded; common sensitive text and URLs are filtered
* **Separate image consent** — optional screenshot pixels are not redacted and may contain private information
* **Local-first** — recordings and inference stay on the operator's machine
* **Visible indicator** — a persistent badge shows while recording
* **User deletion** — recordings carry a TTL and can be deleted at any time
* **Separate delivery consent** — automatic delivery needs its own per-session opt-in, pinned destination, ticket cap, expiry, and bounded retries; manual delivery needs exact preview confirmation
* **Execution boundaries** — delivered proposals still require review; both manual and automatic delivery preserve backlog and no-auto controls. Panther does not implement the work
* **Local trust boundary** — the backend binds to loopback with origin/Host/header checks, but has no user authentication. Do not expose it publicly. Other local processes and installed extensions with appropriate access are inside this single-user trust boundary.

Deletion removes local capture artifacts, drafts, and automation jobs, but cannot undo an in-flight destination write. It does not remove exports, browser consent history, remote tickets, or content-free delivery receipts needed for deduplication. Never include real recordings or screenshots in public bug reports without inspecting them for private data.

## Disclosure

We will acknowledge your report within 72 hours and provide a status update within 7 days.
