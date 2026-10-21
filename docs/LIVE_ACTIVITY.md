# Live activity and interpretation

Panther's status window explains a consented capture as it happens. Turning Panther On alone does not observe the page. Choose an ordinary webpage, open the toolbar popup, grant capture consent, and Start. The status window cannot select another tab or start capture for you.

## What the window shows

- **Observed actions:** a bounded recent history of clicks, control changes, submissions, and navigation with timestamps and evidence numbers. These are observations, not proof that the page responded successfully.
- **What Panther is noticing:** a plain-language summary, the stated goal if supplied, and a tentative interpretation based on recent control semantics. The default interpreter is local deterministic rules, clearly labeled; it is not an AI model.
- **Possible friction:** repeated interactions with an identified control or an A → B → A navigation pattern can prompt a review suggestion. Each suggestion cites the visible events and explains the uncertainty. Normal intentional behavior can produce the same patterns.
- **Freshness:** the last observed event is separate from the status connection heartbeat. An idle page is not made to look active by synthetic events. Stopped, retained, or stale observations are labeled as history.

The projection inspects at most the last 30 stored events and displays at most 12 sanitized observations. Event numbers refer to the original capture. It does not expose typed keys, field values, arbitrary event objects, screenshot pixels, or full page text. Sanitization reduces exposure but does not guarantee that a control label, route, or user-written goal contains no private information.

## Optional live local AI

During an active capture, explicitly select **Live local AI insights** in the status window to let the configured local model interpret those bounded observations. This is independent of screenshot consent, ticket-generation AI, and automatic ticket delivery.

The option defaults off for each window/session. While enabled and visible, the window requests an interpretation only when new captured actions have arrived, at most once every 15 seconds, with one request in flight. It does not retry the same observation revision after an error. The response identifies the model, the evidence numbers and capture revision it considered, and its low or medium confidence. New actions may arrive before a response; the UI distinguishes that older coverage from current observations.

Only the configured loopback Ollama-compatible endpoint is used. Panther does not start or install a model service, and there is no paid or remote fallback. A missing model, slow response, malformed output, or busy local service is shown as unavailable; the observed-action feed keeps working. Local inference uses CPU/GPU resources even though no paid provider is called.

Unchecking the option, hiding/closing the window, losing fresh consented capture status, stopping capture, or turning Off stops further live interpretation and ignores late responses. A request already accepted by the model service may finish its bounded computation; aborting an HTTP request does not promise immediate model cancellation. Off shuts down Panther's owned backend, not the independently running model service.

Live insights are not draft tickets, do not mutate existing drafts, and never submit tickets or authorize work. The existing Stop → generation → optional delivery workflow remains separate. AI output is untrusted proposed interpretation, not an independently verified bug or outcome.

## Privacy and authority contract

The action projection is returned only to Panther's trusted status window, not to a captured page or arbitrary extension sender. Optional inference takes a matching current session ID and explicit consent; the background process constructs the request from its own sanitized capture rather than accepting caller-supplied events. It runs outside the capture queue so events, status, and Off are not blocked by inference.

`POST /api/extension/live-insight` accepts a small allowlisted JSON body with at most 12 numbered observations. Existing loopback Host, Origin, request-header and browser-lease checks still apply. These are local single-user request protections, not authentication against a malicious local program. The endpoint re-sanitizes text and URLs, limits input/output size and model concurrency, requires evidence references to belong to the provided observations, and performs no recording, draft, ticket, or execution writes.

The bounded rules summary is retained with the local saved result so the monitor can show history after Stop. Local AI results exist only in the window's current view; they are not persisted into a capture or sent to a destination. Personal capture data, model output, and screenshots must not be published as public verification fixtures.

## Verification scope

Regression coverage includes projection/redaction limits, evidence identity, sender restrictions, immutable inference snapshots, request throttling, malformed model responses, timeout/cancellation, stale status, and Off/Stop/session-switch races. Browser qualification uses synthetic controls and isolated data. A successful model mock does not establish that a user's configured local model is installed, responsive, or semantically useful.

See the [1.5.0 verification report](LIVE_ACTIVITY_TEST_RESULTS.md) for loaded-browser observations and remaining qualification boundaries.
