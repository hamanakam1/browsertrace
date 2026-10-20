# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅        |

## Reporting a Vulnerability

browsertrace deals with sensitive user data (screen recordings, interaction traces). We take security seriously.

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities.
2. Email the maintainer with details of the vulnerability, steps to reproduce, and any suggested fix.
3. Allow reasonable time for a response and fix before public disclosure.

## Security Principles

browsertrace is designed around these security boundaries:

* **Opt-in capture** — recording never starts without explicit user consent
* **Targeted capture** — only pages you configure in the manifest are captured
* **Secret redaction** — password/token/credential fields are scrubbed before storage
* **Local-first** — recordings and inference stay on the operator's machine
* **Visible indicator** — a persistent badge shows while recording
* **User deletion** — recordings carry a TTL and can be deleted at any time

## Disclosure

We will acknowledge your report within 72 hours and provide a status update within 7 days.
