# Automatic intent-to-ticket delivery

Panther's scope ends at ticket creation and verified delivery. It does not implement fixes, dispatch agents, remove execution fences, or accept completed work.

## Operator experience

Before starting a capture, separately enable automatic delivery to the exact AI Dashboard origin displayed in the popup. Local AI is another optional choice; evidence-only generation needs no model. These choices are per session and default off. The normal Start action and chosen-tab capture consent remain necessary.

After Stop, the extension saves the capture and the backend automatically generates proposals, submits them to Dashboard, and reads each ticket back. The popup, floating controls, and review page show progress and verified counts. **Cancel remaining delivery** prevents further work but cannot retract an in-flight request or delete an existing Dashboard ticket. **Retry now** advances an eligible automatic retry; it does not renew authority or reset the budget.

Automatic output is an **unreviewed proposal**. Recorded actions are evidence of those actions, not proof of a defect. AI hypotheses remain separate from observed reproduction steps. Every created ticket enters backlog with `no-auto`, `needs-triage`, machine provenance, and the explicit denial of autonomous execution. A verified receipt proves that content and those fences matched at its timestamp; it is not continuous monitoring of the ticket's current state.

## Admission contract

`POST /api/extension/record` accepts an optional policy alongside the existing capture body:

```json
{
  "automation": {
    "enabled": true,
    "consent": true,
    "target_url": "http://127.0.0.1:7878",
    "use_ai": false,
    "max_tickets": 5
  }
}
```

This is an excerpt, not a complete upload. The original capture identity, timestamps, events, and capture consent are still required. The target must exactly match the backend's canonical configured loopback origin. The API accepts a ticket limit from one to five; the shipped UI uses five. The policy is immutable on first admission and expires 24 hours after capture Start, not after upload. Replaying an upload cannot change the policy, renew consent, or restore exhausted retries. A delayed upload after expiry is refused. Manual captures cannot be upgraded to automatic delivery by replaying their upload.

Generated proposals are frozen before any submission. If their count exceeds the admitted cap, the whole job blocks before delivery rather than silently dropping proposals or delivering a partial capped subset. Changing draft fields later cannot alter the frozen outbound payloads. Changing the configured destination blocks the job rather than redirecting its data.

## Durability and failures

Jobs are private, atomic JSON records in `PANTHER_DATA_DIR/automation/`. The backend owns one delivery worker and recovers unfinished admitted jobs on startup. It does not need the popup to stay open. An initial missing job can be recreated only from a recording's previously persisted explicit policy. If processing already began, a missing job instead becomes blocked: uncertain history cannot authorize regeneration or reset the attempt budget. The backend never infers consent from a draft or from the mere existence of a recording.

| Job state | Meaning |
|---|---|
| `queued` | Saved and admitted; waiting for the worker. |
| `generating` | Producing observed evidence or explicitly requested local AI proposals. |
| `delivering` | Sending frozen proposals and independently checking readback. |
| `retry_wait` | A retryable failure occurred; waiting for a bounded retry. |
| `delivered` | Every proposal has a verified delivery receipt. |
| `blocked` | Consent expired, attempts exhausted, cap exceeded, destination changed, or another non-retryable condition occurred. New consented capture is required to readmit automatic work. |
| `cancelled` | Remaining work was cancelled; past or in-flight writes may still exist. |
| `disabled` | This is a manual capture, not an automatic job. |

The backend permits four total attempts: the initial attempt and retries after 15, 60, and 300 seconds, all within the original expiry. Refused intake and payload conflicts block; uncertain network/readback results retry with the same recording, draft, and deterministic destination identity. A known destination item is read back rather than recreated. Verified receipts prevent duplicate writes. There is no paid or remote inference fallback.

Before backend admission, the extension has a separate four-attempt upload budget: the initial explicit Stop and up to three retries after one, two, and four minutes, driven by a one-minute browser alarm. Only an already-stopped, explicitly opted-in session is eligible for automatic upload retry. Browser restart never resumes recording or silently sends an interrupted active capture. Other automatic interruptions, such as leaving the chosen origin, closing its tab, or reaching the event limit, preserve the capture and require an explicit upload action. Capture-consent withdrawal must not authorize an automatic send. Upload retries cannot extend the original policy expiry.

Deleting or expiring a capture removes its job and stops remaining work. Content-free delivery receipts survive to preserve duplicate protection; deleting local files cannot undo remote tickets. Cancellation and deletion cannot roll back a network request already in flight.

Cancellation also works when an upload acknowledgment is lost or no recording has arrived yet. The backend persists a content-free cancellation marker for the deterministic capture identity before acknowledging it. Delayed uploads with that identity are refused, including after a backend restart. These markers live under `automation-cancelled/`, expire after the maximum consent window plus five minutes of clock skew, and are removed when that identity is checked after expiry. An acknowledged missing-upload cancellation is not a claim that any ticket was delivered.

## Operating limits

Keep the backend running, or restart it to recover queued jobs before expiry. Panther does not install a login service, start AI Dashboard, run around a Dashboard hold, or grant browser permissions itself. Automatic delivery currently targets the supported **local AI Dashboard** only; other systems can consume the versioned JSON bundle but need their own destination adapter and explicit data-sharing authorization.

The loopback API's origin/header checks are not user authentication. This is a single-user local tool, not a network service. Do not expose its listener publicly.
