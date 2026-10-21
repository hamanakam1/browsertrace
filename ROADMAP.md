# Panther Intent Capture roadmap

The product goal is to make a user's observed workflow and desired change understandable to another system, with AI Dashboard as the first destination. Capture, interpretation, human review, delivery, and execution remain distinct steps.

## Implemented in the current source

- Explicit chosen-tab capture for Chrome/Edge 116+ on ordinary HTTP(S) pages.
- Events-first capture with a floating status/control panel and popup recovery actions.
- Allowlisted control metadata; no typed values or keystrokes; URL and common-secret sanitization.
- Separately consented screenshots, bounded to five per session. Pixels remain unredacted.
- Durable browser capture state, idempotent backend upload, interrupted-upload recovery, and separate draft-generation retry.
- Private local storage, retention limits, and capture deletion.
- Automatic evidence drafts after Stop, without model inference.
- Explicit local AI generation with provenance and visible errors.
- Review UI with editable title, description, and acceptance criteria.
- Destination-neutral, versioned JSON export with evidence references.
- AI Dashboard preview and confirmed single-draft submission, deterministic identity, durable delivery receipts, and independent readback.
- Backlog and `no-auto` fences on dashboard submissions; no execution authorization.
- Synthetic browser-controller, HTTP, storage, inference, and delivery contract tests.

These are implementation claims. Synthetic tests are not evidence of loaded-extension acceptance, live model quality, live dashboard delivery, or cross-site compatibility.

## Next: qualify the complete user workflow

- Exercise the unpacked extension in fresh Chrome and Edge profiles, including same-origin reloads, SPA routes, tab changes, service-worker suspension, and browser restart.
- Verify the popup and floating panel remain usable with long text, zoom, keyboard navigation, screen readers, and narrow windows.
- Run consented AI Dashboard captures and verify useful, edited tickets from preview through live readback without allowing autonomous execution.
- Measure draft usefulness with representative examples: accurate evidence, clear expected behavior, useful acceptance criteria, and minimal manual correction.
- Test an explicitly configured local model and report model/version, errors, latency, and whether any media was analyzed.
- Establish a small, published compatibility matrix from actual website/browser checks.

## Next: make integrations easier to extend

- Publish a formal JSON Schema and version-migration policy for intent bundles.
- Add destination adapters behind the same preview, confirmation, idempotency, and delivery-receipt contract.
- Prioritize a GitHub issue adapter and a configurable local importer after the AI Dashboard workflow is qualified.
- Add destination setup and capability discovery without exposing credentials to captured pages.
- Provide deliberate update/link-existing workflows; resubmitting edited content must not silently overwrite an existing ticket.
- Improve evidence transfer and attachments with separate consent, access control, and clear retention behavior.

## Next: stronger privacy and operational controls

- Pair or allowlist extension installations instead of treating extension-origin checks as authentication.
- Add redaction review and screenshot masking before storage or export.
- Provide explicit receipt/history retention controls while explaining their effect on duplicate prevention.
- Improve large-session coverage, event truncation disclosure, and cross-frame capture with explicit permission boundaries.
- Package startup/service installation only after testing lifecycle, path configuration, upgrades, and uninstall behavior.

## Deferred capabilities

- Video and audio capture, replay, and synchronized media evidence. These remain disabled in the current release.
- Firefox and Safari builds. Existing manifests are unsupported previews.
- Native desktop capture and operating-system integrations. Browser support does not imply arbitrary application support.
- Streaming inference, session comparison, heatmaps, and DOM reconstruction.
- Automatic coding-agent execution. A captured workflow or delivered ticket never grants permission to run code, change a service, or publish work.

## Design commitments

Capture requires an explicit start on a chosen page. Evidence and inference must be distinguishable. Failures remain visible and retryable. Review comes before delivery, and delivery remains separate from execution. Local defaults and deliberate exports preserve the user's control over where captured information goes.
