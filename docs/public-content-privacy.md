# Public-content privacy check

Public documentation must remain deployment-neutral. This check reads a private
GitHub Actions repository secret named `PUBLIC_CONTENT_BLOCKLIST_JSON`: a JSON
array of literal strings, not regular expressions. The actual entries must never
appear in this repository, examples, issue bodies, logs or public test fixtures.

## What runs

- Pushes to main audit the current tracked tree. Pull requests are inspected before
  merge through `pull_request_target`, using the checker from the trusted base.
- Candidate Git blobs are read as data. Candidate code, actions, hooks, dependency
  installers, caches and tests are **not executed** in the secret-bearing workflow.
- The secret exists only in the scan step. The status token exists only in status
  reporting steps. Checkout credentials are not persisted. The checker uses only
  Python's standard library; actions are pinned to immutable commits.
- The commit status `public-content-privacy` is written on the exact candidate SHA.
  It is successful only after a complete scan. Missing/invalid configuration, object
  errors and scan limits fail closed. Configure this status as required on main to
  enforce it; the workflow by itself does not prevent direct pushes or publication.
- Changes beneath `.github/` or to the checker and its tests fail the automatic PR
  scan. A maintainer must review them, then manually run **Public content privacy**
  from **main**, supply the PR number, and confirm `reviewed_control_changes`.
  Review all workflow changes for secret access, not just changes to this guard.
  The authorization applies to the fetched SHA, not later pushes to the PR.

## Matching and output

The scanner checks every tracked filename, regular file and symlink target using
case-insensitive, Unicode-normalized literal matching over UTF-8 and UTF-16 text.
It reports matching file paths, never matched entries or excerpts. A path that
itself matches is withheld; other paths are JSON-escaped to prevent log injection.
Tests use invented words only. Do not replace them with real deployment identifiers.

The limits are 20,000 files, 16 MiB per blob and 256 MiB total. Submodules and LFS
pointers fail closed because their external content is not inspected. Images and
other binaries are checked for embedded literal text only: **no OCR, archive
decompression, general secret detection or semantic privacy judgment is provided**.
Encoded, split or transformed identifiers may evade literal matching.

This is a guard against accidental republication, not a confidentiality boundary
against hostile repository administrators or an oracle-proof secret store. A PR
author can observe pass/fail and infer guessed entries. Keep credentials out of
the blocklist; this is a list of private naming patterns, not passwords or keys.
Maintainers with workflow-write access remain trusted. A protected main branch
still does not prevent someone from putting sensitive content in a public branch,
PR body, issue, release, image or pre-existing history. Review those separately.

## Private configuration and maintenance

From a trusted administrator machine, create a mode-0600 JSON file **outside this
repository**. Feed it on stdin, never as a command-line argument:

```sh
gh secret set PUBLIC_CONTENT_BLOCKLIST_JSON --repo OWNER/REPO < /private/path/blocklist.json
gh workflow run public-content-privacy.yml --repo OWNER/REPO --ref main
```

Do not print the file or upload it as an artifact. Updating the secret does not
rerun old checks: rerun audits for main and relevant open PRs. Fork PRs are scanned
without executing fork code. Some bot-triggered runs may not receive secrets; a
maintainer can manually scan that PR from main rather than weakening the check.

Run the isolated detector tests with:

```sh
python3 -I -m unittest discover -s tests -p test_public_content_guard.py -v
```

If the guard is broken, keep the required status failing while repairing it through
a reviewed change. Do not publish the private list as a workaround. Rollback is a
normal reviewed revert of the guard files, with an explicit maintainer decision
about enforcement; reverting does not erase existing public history.
