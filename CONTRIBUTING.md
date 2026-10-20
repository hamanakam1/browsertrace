# Contributing to panther-intent-capture

Thank you for considering a contribution! panther-intent-capture is an open-source browser extension that observes page usage with explicit consent and runs local inference on recordings. We welcome improvements of all sizes.

## How to contribute

1. **Fork the repo** and create a branch: `git checkout -b your-feature-name`
2. **Make your changes** — keep them focused and well-tested
3. **Run the checks** — see "Verification" below
4. **Open a pull request** — describe what you changed and why

## Verification

### Extension
- Load the unpacked extension in Edge/Chrome/Firefox and verify recording starts/stops with consent
- Confirm the visible badge appears while recording
- Confirm secret fields (e.g. `password`, `token`) are redacted in stored events

### Backend
```bash
cd backend
./start.sh
curl http://127.0.0.1:7890/api/extension/health
```

### Unit tests
```bash
# Extension redaction logic (Deno)
deno test tests/redaction_test.mjs
```

## Code style

- JavaScript: ES modules, `import`/`export`, no `var`
- Python: type hints where practical, black-compatible formatting
- Commit messages: imperative mood, concise ("Add video range support", not "Added video range support")

## Privacy-minded contributions

Any feature that captures or stores user data must:
- Be opt-in
- Redact secrets before storage
- Show a visible indicator while active
- Allow user deletion

## Questions

Open an issue or start a discussion. We're happy to help.
