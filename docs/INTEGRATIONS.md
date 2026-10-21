# Integration contracts

Panther separates browser capture and draft review from destination delivery. AI Dashboard is the current built-in destination. A versioned JSON intent bundle is the integration point for other systems; there is no universal API adapter or native application capture in this release.

## Portable intent bundle

Choose **Save draft edits**, then **Export JSON** in the review UI. Exports contain the currently saved drafts, source metadata, bounded evidence references, and explicit authority flags. They do not contain screenshot image data, video files, raw event arrays, local filesystem paths, or destination credentials.

The export has `schema_version: "1.0"`. The `schema` value identifies this documented contract; it is not a hosted JSON Schema validator. This abbreviated synthetic example uses the actual field names:

```json
{
  "schema": "https://github.com/hamanakam1/panther-intent-capture#intent-bundle",
  "schema_version": "1.0",
  "producer": "panther-intent-capture",
  "exported_at": 1792537200,
  "recording": {
    "id": "example-capture",
    "source_url": "https://example.test/settings",
    "title": "Settings",
    "stated_goal": "Remember my selected filter",
    "capture_mode": "events",
    "start_time": 1792537100,
    "end_time": 1792537130,
    "duration_ms": 30000,
    "event_count": 3,
    "screenshot_count": 0
  },
  "tickets": [
    {
      "id": "draft-example",
      "title": "Remember the selected filter",
      "description": "The selected filter resets when returning to this page.",
      "hypothesis": "",
      "steps_to_reproduce": ["Select a filter", "Leave the page", "Return"],
      "suggested_verification_steps": [],
      "acceptance_criteria": ["The selected filter is restored on return"],
      "desired_behavior": "Remember my selected filter",
      "questions": [],
      "component": "unspecified",
      "priority": "medium",
      "evidence": "Three recorded navigation and control events; verify the reported reset.",
      "evidence_refs": [{"kind": "event", "event_index": 1, "type": "change", "timestamp_ms": 1792537101000}],
      "screenshot_timestamps": [],
      "source_url": "https://example.test/settings",
      "provenance": {
        "origin": "observed_evidence",
        "producer": "panther-intent-capture",
        "recording_id": "example-capture",
        "mode": "observed",
        "model": null,
        "generation_revision": 2,
        "evidence_kind": "observed_events",
        "proposal_verified": false,
        "event_count": 3,
        "events_total": 3,
        "events_truncated": false,
        "screenshots_count": 0,
        "media_analyzed": false
      },
      "status": "draft",
      "requires_review": true,
      "execution_authorized": false
    }
  ],
  "authority": {"export_only": true, "requires_review": true, "execution_authorized": false}
}
```

Recording times are Unix seconds; event references use Unix milliseconds. `event_index` is one-based within the observed event list. A reference is evidence provenance, not proof of the proposed change or a promise that an image is embedded. Source URLs are sanitized. Some optional metadata may be missing from older captures.

Observed drafts summarize actions and the supplied goal without a model. AI drafts identify their model, mode, and generation revision. Their `hypothesis` and `suggested_verification_steps` are unverified proposals kept separately from `steps_to_reproduce`, which is derived from captured events. `proposal_verified: false` means the proposed problem has not been established by those events. Preserve those distinctions in downstream tools. Preserve unresolved questions and missing acceptance criteria instead of inventing certainty. Draft IDs remain stable for the recording, generation mode, and draft position; editing a draft does not create a new identity.

## AI Dashboard adapter

The default target is `http://127.0.0.1:7878`. Set `PANTHER_DASHBOARD_URL` before backend startup to use a different loopback HTTP(S) origin. Paths, query strings, credentials, non-loopback hosts, and redirects are rejected. `localhost` is canonicalized to `127.0.0.1`, and environment HTTP proxies are disabled for adapter requests.

The backend calls these existing dashboard routes:

| Operation | Dashboard route | Meaning |
|---|---|---|
| Status | `GET /healthz` | Reachability only; no ticket data is queried. |
| Create or deduplicate | `POST /api/master/add` | Create a review-fenced backlog item using a stable seed. |
| Verify | `GET /api/tix/ticket?key=<returned-key>&seen=0` | Confirm the exact item without marking its conversation as read. |

Panther's extension does not call the dashboard directly. The dashboard permits Origin-less local automation but rejects extension browser origins on its board-write routes. Panther retains that boundary and does not enable broader dashboard CORS.

The outbound ticket has:

- `seed_key`: `panther:capture:<recording-id>:<ticket-id>`.
- `source`: `panther-intent-capture:agent-proposal`.
- `status: "backlog"`, `tags: ["panther", "needs-triage", "no-auto"]`.
- A detail starting with `not authorized for autonomous execution`.
- Title, intent summary, source URL/times, provenance, observed evidence, reproduction steps, acceptance criteria, and evidence references.

The adapter respects the dashboard's 200-character title and 4000-character detail limits. The preview shows the bounded payload and warns that the full draft remains in the neutral export. It does not upload screenshots or set the dashboard's separate readiness record to `NEEDS_REVIEW`.

Submission requires a review confirmation tied to the current preview hash, including its destination URL, exact outbound payload, and full reviewed draft. Changing the destination or payload requires a new preview. The backend verifies the returned item number, capture identity, source, content, backlog status, and review tags before first reporting `delivered`. HTTP 200 or `ok: true` alone is insufficient: the dashboard can return a null item when intake is paused or capped.

### Delivery outcomes and retry behavior

| State | Meaning | Next action |
|---|---|---|
| `delivered` | Creation/deduplication and independent content readback matched at `verified_at`. | Open the ticket for dashboard review. Repeating the same request reuses the receipt. |
| `pending` | A request or readback was uncertain; delivery is unverified. | Retry the same draft. If the ticket ID is known, only readback is retried. |
| `refused` | No valid item ID was returned, including intake holds/caps. | Resolve the refusal, then retry with the same identity. |
| `conflict` | This identity was submitted with different content. | Review the existing dashboard item or deliberately create a distinct draft. |

An unknown POST result may mean the dashboard already created the item. A retry keeps the same deterministic seed so the dashboard can return that existing item. Never generate a new seed merely because a request timed out. A `created: false` response is valid when it includes the existing item and its readback matches.

A reused delivered receipt returns `previously_verified: true` and retains its original `verified_at` timestamp. It performs no new dashboard request and does not establish that the ticket's current content or execution fences are unchanged. Clients must describe that result as previous verification, not a fresh check.

Receipts are private JSON files under `PANTHER_DATA_DIR/deliveries/`, written atomically before the request. They retain the target, seed, content hash, state, and returned item metadata. A receipt write failure prevents the initial POST. Receipts remain after deleting a capture so a later duplicate attempt can still be recognized. Capture deletion does not delete dashboard tickets, exports downloaded elsewhere, or these receipts. Removing a receipt is not a supported ticket-delete or ticket-update operation.

## Panther HTTP API

The backend binds to `127.0.0.1:7890` by default. `/` serves the review UI. API routes begin with `/api/extension/`.

| Method and route | Purpose |
|---|---|
| `GET /health` | Backend identity and advertised capabilities. |
| `POST /record` | Upload a consented capture with stable session identity. Used by the extension. |
| `GET /recordings` | List capture summaries. |
| `GET /recordings/<id>` | Get one capture's public metadata. |
| `GET /recordings/<id>/events` | Inspect its sanitized local event evidence. |
| `GET /recordings/<id>/screenshots` | Inspect separately consented local screenshot pixels. |
| `DELETE /recordings/<id>` | Delete local capture artifacts and drafts. |
| `POST /tickets/<id>` | Generate an observed draft by default; `{ "use_ai": true }` explicitly requests local AI. |
| `GET /tickets/<id>` | Read the saved draft bundle. |
| `POST /tickets/<id>/review` | Save allowed draft edits. |
| `GET /export/<id>` | Export the portable JSON bundle. |
| `GET /integrations/ai-dashboard` | Check dashboard health-route reachability. |
| `POST /integrations/ai-dashboard/preview/<id>` | Build the exact dashboard payload without contacting it. |
| `POST /integrations/ai-dashboard/submit/<id>` | Submit one confirmed, previewed draft and verify it. |

POST and DELETE requests require an `X-Panther-Client` header. JSON POST requests use `Content-Type: application/json`. Browser requests must come from a permitted extension origin or the backend's loopback origin; unrelated website origins are refused. This header is not a secret or authentication token. The API is intended for a single-user local environment.

Review and preview bodies use:

```json
{
  "ticket_id": "draft-example",
  "patch": {
    "title": "Remember the selected filter",
    "description": "The selected filter resets when returning to this page.",
    "acceptance_criteria": ["The selected filter is restored on return"]
  }
}
```

Submit repeats the reviewed body and adds `"confirmed": true` plus the exact `"payload_hash"` returned by preview. Changing the content invalidates that preview. The shipped UI handles this sequence; custom clients must preserve the same explicit review gate.

## Building another adapter

Keep the capture and neutral export schema unchanged. Map a reviewed draft to the target system's supported API, retaining source evidence, the stable capture/draft identity, authority flags, and a durable delivery receipt. Provide a preview, require explicit confirmation, use the target's native idempotency capability, and verify the returned object. Report uncertain delivery rather than silently retrying under a different identity. Make deliberate edits to already delivered items a separate operation.

Remote destinations need their own authentication, data-sharing consent, destination validation, and credential storage design. The built-in dashboard adapter intentionally remains loopback-only; do not relax its host checks to turn it into an arbitrary webhook client.

## Verification boundary

`tests/test_integrations.py` uses a fake loopback dashboard and temporary storage to test the adapter contract, including lost responses, deduplication, readback mismatches, redirects, and refused intake. A successful fixture run does not prove compatibility with a running dashboard version or acceptance of a real ticket. Record browser installation, actual captured evidence, the operator's delivery confirmation, and live readback separately when qualifying a deployment.
