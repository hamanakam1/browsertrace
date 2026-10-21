# Panther 1.1.0 verification

Tested on 2026-10-21. This report distinguishes automated contract tests, a real browser smoke test, and work that still needs a consented pilot. It is not a claim that every website, browser, or destination works.

## Automated checks

Local environment: macOS arm64, Node 26.11.1, Python 3.14.6.

| Check | Result | Scope |
|---|---|---|
| `node --test tests/*.mjs` | **39 passed, 0 failed** | Capture state, popup/content behavior, consent, redaction, sender validation, recovery, screenshot limits/warnings, scoped manifests. Browser APIs are doubled. |
| `python3 -B -m unittest discover -s tests -p 'test_*.py'` | **50 passed, 0 failed** | Storage, generation, HTTP, privacy boundaries, fake inference/dashboard servers, delivery receipts and retry contracts. |
| `python3 scripts/package_extension.py` | **Passed** | Valid Chromium 1.1.0 extension ZIP with 18 files and referenced resources. |
| `git diff --check` and JavaScript syntax checks | **Passed** | Patch whitespace and syntax, not runtime acceptance. |

The Python suite covers timestamp conversion, stable recording identity, atomic private storage, cleanup, binary multipart preservation, consent enforcement, origin checks, missing models, malformed AI output, evidence ordering, reviewed-edit preservation, and older unsafe AI-cache rejection. Adapter tests cover uncertain POST results, refusal with null item IDs, independent readback, content/fence mismatches, no duplicate POST after verified delivery, and destination-bound preview confirmation.

GitHub Actions runs the suites on Linux with Node 22 and Python 3.11/3.14. See the [CI runs](https://github.com/hamanakam1/panther-intent-capture/actions/workflows/ci.yml) for commit-specific results and the extension ZIP artifact; this document does not substitute for a completed CI run.

## Real Chromium smoke test

Used an isolated Chromium profile with the unpacked extension, a temporary backend data directory, and synthetic interactions on Panther's own local review page. No private Dashboard page was recorded.

Verified in the browser:

- The unpacked Manifest V3 worker loaded and injected the chosen top-frame content script.
- The floating panel showed capture status and an event count; its visible **Stop & generate** control saved the recording and produced one evidence draft automatically.
- Actual click and hash-navigation events appeared as observed steps.
- **Review drafts** opened the correct recording in the extension review page.
- Title and acceptance-criteria edits saved and survived switching between observed and AI draft modes.
- The Dashboard preview displayed the reviewed content and execution fence. It was canceled without submission.
- **Export JSON** downloaded the saved draft bundle with the edited title and evidence provenance.
- At a 390-pixel viewport, the preview's submit button was reachable and unobscured. Long observed URLs wrapped without horizontal overflow.
- Screenshot permission denial was shown in both the floating panel and popup while event capture, saving, and draft generation still succeeded.
- The consent checkbox could be checked and unchecked without the original transient snap-back after its regression fix.

The popup was opened as an extension page for automation, and Start was invoked through its real extension messaging API while the test page was active. This does **not** reproduce a native toolbar click's `activeTab` permission grant. Successful screenshot capture on arbitrary websites therefore remains unverified. The permission-denial path was exercised rather than counted as screenshot success.

## Local AI smoke test

An already-installed local Ollama `qwen2.5:7b` model generated proposals from the synthetic capture. No paid provider, fallback, or model download was used. This validates only that model/configuration on the test machine, not every model or the default model setting.

The initial model output invented narrative evidence. That test drove the generation-revision-2 boundary: observed steps now come only from validated captured-event references in event order; model hypotheses and suggested verification steps remain explicitly unverified and separate. The corrected generation was exercised again in the real review UI. Generated suggestions still need human review and are not proof that a defect exists.

## Not yet qualified

- A normal toolbar-initiated capture in the operator's Edge profile, including successful screenshot permission and pixels.
- Real-world workflows on AI Dashboard and representative third-party sites, including site-specific SPA and frame behavior.
- An explicitly confirmed write to the live AI Dashboard and its independent readback. Only the live health route was checked for reachability; all delivery tests used fake servers.
- Long-duration/resource use, actual forced browser termination, accessibility with assistive technology, Windows/Linux desktop UI, and extension-store installation.
- Firefox, Safari, native desktop applications, video/audio, and built-in GitHub/Jira/Linear destinations. These are not supported capabilities of this release.

## First pilot

Start the backend with a private data directory, load `extension/` in Chrome or Edge, open a harmless workflow, and use Panther's actual toolbar button. Confirm capture consent, leave screenshots off initially, reproduce the workflow, stop, and inspect every observed step. Then test separately consented screenshots. For AI Dashboard, inspect the exact preview and confirm one clearly labeled pilot ticket only when its content is safe to share; verify the returned ticket and review fences independently. Capture consent does not grant autonomous execution authority.
