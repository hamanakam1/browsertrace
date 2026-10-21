# Panther 1.3.0 browser-control verification

Tested on 2026-10-21. This release adds explicit browser On / Off; it does not install login startup or authorize ticket execution.

## Automated checks

Local environment: macOS arm64, Node 26.11.1, Python 3.14.6, jsonschema 4.26.0.

| Check | Result |
|---|---|
| `node --test tests/*.mjs` | **118 passed, no failures or skips** |
| `python3 -B -m unittest discover -s tests -p 'test_*.py'` | **118 passed, no failures or skips** |
| `python3 scripts/package_extension.py` | **1.3.0 ZIP validated, 20 extension files** |
| `git diff --check` and changed JavaScript syntax checks | **Passed** |

The JavaScript suite includes 16 lifecycle tests, actual background-dispatch tests, popup races, capture interruption, and options-page power guards. The Python suite includes 19 native protocol/lifecycle cases and seven installer cases. Subprocess tests use temporary data, injected ephemeral ports, and fake services, not the operator's live Dashboard.

Specific shutdown regressions verify:

- Startup, alarms, popup polling, disconnect, and spoofed page messages do not start a native host.
- Missing helpers, malformed frames, mismatched origins, unsafe configuration permissions, and occupied ports fail visibly and do not adopt another process.
- Off during startup, upload, generation, or stalled response parsing cannot allow a late response to restart work.
- Native shutdown proceeds even when browser storage is stalled; remaining local-persistence uncertainty is reported separately.
- Normal popup status polling detects a dead owned child through the existing native connection without reconnecting.
- Revocation after receipt persistence but before network submission results in **zero destination POSTs**. An already in-flight POST may finish; its delivery receipt is preserved.
- Native-host EOF/process death closes the owned listener. An internal per-On session fence prevents a browser job from resuming through a later manual server after abrupt child death.
- Installer reruns are idempotent only for unchanged, correctly owned/private files. Another registration, symlinks, edited files, unsafe modes, and remote service URLs are refused. Uninstall preserves captures.

An independent audit reproduced the pre-send, stalled-persistence, and dead-child-status defects before their fixes, then reran those same cases successfully afterward. See [commit-specific CI](https://github.com/hamanakam1/panther-intent-capture/actions/workflows/ci.yml) for Linux Node 22 / Python 3.11 and 3.14 results; the table above is local verification, not a substitute for CI completion.

## Real isolated-browser checks

Used Google Chrome for Testing with an unpacked extension, a separate browser user-data directory, a helper registered only in that temporary directory, and temporary capture storage. No personal browser registration was modified by this test.

- Before registration, clicking On produced the actual native-host-not-found error with setup instructions; no backend listener appeared.
- With the helper registered, the popup remained Off until its visible **Turn Panther on** button was clicked. The click launched a real owned backend, confirmed by its loopback health response. Capture consent remained unchecked.
- In a fresh final-source profile, separate capture consent and an explicit Start captured two real navigation/click events on Panther's harmless local review page. Screenshots and automatic ticket delivery were off.
- Clicking the visible **Turn Panther off** button during capture left those two events pending in browser storage, cleared capture consent, recorded zero upload attempts, and closed the backend listener. The confirmed power response had an empty error field.
- Clicking On again did not resume capture or upload. Capture consent was still unchecked, and the backend capture library was empty.
- Quitting that browser process while On closed the backend listener. Reopening the same isolated profile left Panther Off, capture consent cleared, and the same two pending events preserved with zero upload attempts.
- The popup was checked at a 360-pixel viewport with no horizontal overflow. On and Off controls are near the top and remain reachable. Screenshots were visually reviewed and retained locally, not committed with browser data.

The popup was opened as an extension page for automation. Its On/Off buttons used the actual native messaging path. Capture Start used the real extension messaging API with the synthetic local tab active; this is not evidence of a native toolbar `activeTab` permission grant or third-party-site screenshot success.

No live Dashboard ticket was created in this lifecycle test. The earlier [1.2.0 delivery pilot](AUTOMATION_TEST_RESULTS.md) remains separately documented; its restart-recovery behavior must be interpreted with the stricter 1.3.0 browser power boundary. Turning Off now cancels remaining browser-admitted jobs rather than scheduling their automatic resumption.

## Remaining operator qualification

Registration in a personal Edge profile requires that installation's exact extension ID and an extension reload. Browser-control automation cannot navigate the operator's restricted extension-management page, so that step is a manual handoff, not a claimed installation. Native browser permission prompts remain operator-controlled.

Windows helper packaging, personal-profile toolbar capture, representative third-party applications, screenshot success, and assistive-technology testing remain unqualified. Closing only a browser window may leave its background process running; use explicit **Turn Panther off**. No login service is installed.
