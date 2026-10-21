# Live understanding verification — 1.5.0

Verified 2026-10-21. This report distinguishes regression tests, loaded-browser behavior, and unqualified real-model usefulness. All browser observations used synthetic controls and isolated temporary state; no personal capture or deployment evidence is published.

## Automated checks

- 236 JavaScript tests passed, with no failures or skips (`node --test tests/*.test.mjs`).
- The full Python suite, including JSON Schema validation, passed; see the commit's CI run for platform-specific totals.
- Packaging produced a version 1.5.0 Chromium ZIP containing 26 files. ZIP integrity passed, including the new live-activity module and monitor assets. No extension permission was added.
- `git diff --check` passed.

Coverage includes bounded projection, repeated-control identity, malformed retained state, privacy filtering, trusted monitor sender restrictions, independent capture and inference lanes, current-consent checks, immutable inference snapshots, throttling, visible-evidence validation, cancellation, status freshness, and late-result fencing after Off, Stop, hidden windows and session changes.

## Loaded Chromium observations

Used Chromium 149 on macOS with an isolated profile, helper registration, data directory, backend port, and mock loopback model endpoint. The only runtime adaptations in the test copy were the backend port and corresponding extension host permission. No ticket destination was configured. No real model or paid provider was called.

1. Opening the monitor while Off showed no capture. Explicit On started only the test backend; its health endpoint independently responded. On alone remained Idle with zero events and no model request.
2. A synthetic ordinary webpage was admitted with explicit capture consent through the popup's runtime command path. Actual content-script click events increased the visible count and appeared as numbered, timestamped actions.
3. Clicking an identified Save control three times produced a tentative save/apply interpretation and a repeated-click hypothesis citing precisely those three events. It did not claim that saving succeeded or that a defect was confirmed. The stated goal remained separate from observed behavior.
4. The feed updated after a subsequent click without a monitor reload. Event freshness advanced only from recorded activity; the status heartbeat did not create fake events.
5. Selecting Live local AI insights produced a response labeled with the synthetic model, model-rated low confidence, evidence references and analyzed event coverage. A subsequent new event refreshed the interpretation. Unchanged observations did not cause repeat model calls. The mock was called only after opt-in.
6. Disabling the option and reloading the monitor left AI off. A fresh opt-in after another event produced a new bounded response. No draft, ticket or execution request was made by live interpretation.
7. At 400×620 and 320×620, there was no horizontal overflow. The behavioral interpretation was fully visible without scrolling. At 320 pixels, the sticky Off button remained visible after scrolling to the bottom. Rendered rule, AI and retained-history screenshots were visually inspected and retained locally only.
8. Off stopped the test backend, unchecked and disabled live AI, and relabeled the retained rule and AI outputs as historical. The personal backend on its separate port remained running. Subsequent On did not resume recording, and another fixture click did not increase the retained count.

## Boundaries

- This was a headless loaded-extension check, not physical toolbar interaction in a personal Edge profile. Chosen-page admission used the popup's trusted runtime command path on a synthetic loopback page, not a physical `activeTab` grant. The monitor page was rendered in the isolated browser; persistent window creation/focus was qualified separately in the 1.4.0 report.
- Mock responses verify request/response integration and UI provenance, not whether a configured local model is installed, responsive, accurate or useful. No semantic model benchmark or personal model qualification is claimed.
- Rules infer limited possibilities from recent semantic controls; they cannot verify page outcomes, read all applications, or confirm defects. Typed values, screenshot pixels and full page contents are not supplied to live interpretation.
- No live destination delivery, work execution, screen-reader qualification, cross-browser acceptance, or broad website-compatibility claim follows from these checks.
- The temporary backend was turned Off, and the test browser/helper registration were cleaned up. Captures in the personal browser and existing personal helper configuration were not changed.
