# Panther 1.2.0 automation verification

Historical report for 1.2.0. See [1.3.0 browser-control verification](BROWSER_CONTROL_TEST_RESULTS.md) for explicit On/Off behavior and newer test totals. Browser power is now a stricter boundary: Off cancels remaining automatic jobs, and the next On does not resume abandoned delivery.

Tested on 2026-10-21. This public report separates automated regression coverage and loaded-browser checks against a synthetic destination. Deployment-specific evidence is not published. Panther captures intent and creates tickets; it does not implement or dispatch their work.

## Automated checks

Local environment: macOS arm64, Node 26.11.1, Python 3.14.6, test-only jsonschema 4.26.0.

| Check | Result | Scope |
|---|---|---|
| `node --test tests/*.mjs` | **81 passed, 0 failed, 0 skipped** | 65 capture/content/popup/redaction cases and 16 options-page cases, using browser API/DOM doubles. |
| `python3 -B -m unittest discover -s tests -p 'test_*.py'` | **92 passed, 0 failed, 0 skipped** | Storage, generation, schema, HTTP, adapter, and 25 automatic-delivery cases using temporary storage and fake loopback services. |
| `python3 scripts/package_extension.py` | **Passed** | Valid Chromium 1.2.0 ZIP with 19 files, including the automation status module. |
| `git diff --check` and JavaScript syntax checks | **Passed** | Whitespace and syntax checks, not runtime acceptance. |

New regression coverage includes immutable session consent and destination, original-start expiry, over-cap refusal, frozen proposal hashes, bounded retry budgets, restart recovery, lost acknowledgments, duplicate prevention, active-job edit locks, cancellation during delivery, delayed-upload cancellation, deletion with retained receipts, and missing/corrupt job history. UI regressions cover stale poll races, pending controls, hidden-page polling, screenshot-load isolation, distinct receipt timestamps, and failure recovery.

The tests use synthetic data. They do not contact the live Dashboard or invoke a paid model. See the [commit-specific GitHub Actions runs](https://github.com/hamanakam1/panther-intent-capture/actions/workflows/ci.yml) for Linux Node 22 / Python 3.11 and 3.14 results and the packaged extension artifact; this report does not replace CI readback.

## Loaded-browser recovery test

Used an isolated Google Chrome for Testing profile with the unpacked extension, a temporary backend data directory, and actual click/navigation events on Panther's own local review page. Capture consent and automatic-delivery consent were separately enabled. Screenshots and local AI were off. No private application page was recorded.

Verified:

1. The real extension worker injected the top-frame capture script and showed a small floating status panel with an event count.
2. The visible **Stop & deliver tickets** control saved the capture. The backend generated one evidence proposal and attempted delivery without manual draft submission.
3. The fake destination accepted the first ticket but deliberately dropped the response. Panther showed a retryable failure rather than claiming success.
4. The isolated browser was closed and the temporary backend stopped before the scheduled retry. Restarting the backend with the same data recovered the job automatically.
5. The recovered attempt used the same destination identity. The fake server observed **two POST attempts but exactly one ticket**. Independent readback produced a verified receipt.
6. Reopening the browser showed the completed delivery and its historical verification timestamp. No manual retry click was needed.

The review page was checked at 390 × 844 pixels with no horizontal overflow. Its automation status and controls remained reachable. The popup was checked at 360 × 600 pixels; a discovered width overflow was fixed and rechecked. Status, **Check delivery status**, and **Review drafts** are visible near the top without horizontal scrolling. Desktop and narrow-window screenshots were visually reviewed and retained locally, not committed with captures.

## Live destination qualification

Public fixtures do not establish compatibility with a user's live deployment. Qualify an explicitly configured destination with a harmless capture and separate delivery consent, then independently verify the returned identity, content, and review fences. Keep operational tickets, private deployment identifiers, screenshots, and receipts out of the public repository. A verified receipt records a past check, not continuous monitoring or execution authorization.

## Remaining qualification

- Native toolbar-initiated capture and its `activeTab` permission grant in the operator's normal Edge profile. The automated browser test opened the popup as an extension page and started capture through the real extension messaging API with the local test page active.
- Successful separately consented screenshot capture on ordinary third-party websites. Screenshot-denial handling was checked in the earlier [1.1.0 report](TEST_RESULTS.md), not counted as successful pixels.
- Real local ticket service-page workflows, representative third-party sites, site-specific SPA/frame behavior, and ticket usefulness.
- Automatic local-AI generation with a live model. The new automatic pipeline used evidence-only generation; the older manual local-model smoke test is documented separately in the 1.1.0 report.
- Login-service installation, long-duration/resource use, hardware power loss, assistive technology, and Windows/Linux desktop UI.
- Firefox, Safari, native desktop applications, audio/video, and built-in GitHub/Jira/Linear destinations remain unsupported. Other systems can consume the versioned JSON bundle, but require their own adapter/importer.

The backend must remain running or be restarted within the original consent window. This verification did not install a persistent service or change the operator's personal browser extension installation.
