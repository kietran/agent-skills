---
name: beca-logtime
description: Set up, troubleshoot, and use BecaWork logtime across Windows, macOS, and Linux. Use when the user mentions BecaWork setup or login, tasks, comments, timesheets, daily logtime, existing logtime edits, task status, or percent done through work.becawork.vn.
---

# BecaWork Logtime

Use the bundled CLI for BecaWork operations. Keep command details out of the conversation unless they help diagnose a problem; users should be able to speak naturally.

## Runtime and first use

- Require Python 3.11 or newer. Locate the script relative to this `SKILL.md`; do not hardcode `~/.codex`. Prefer the active Python executable, then try `python3`, `python`, and Windows `py -3`.
- For any BecaWork request, let the CLI readiness gate detect missing setup. If it returns `SETUP_REQUIRED`, open an interactive terminal and run `setup`; never ask the user to paste a password into chat.
- `setup`, `doctor`, `whoami`, and `check-contracts` are read-only. After setup, tell the user which account was verified and suggest a few natural-language requests.
- Read [references/onboarding.md](references/onboarding.md) for setup, credentials, platform behavior, or authentication errors. Read [references/troubleshooting.md](references/troubleshooting.md) when `doctor` or an error code reports a problem.

## Safety

- Never submit or edit logtime, task status, or percent done without explicit confirmation in the current turn.
- Preview and validate the complete proposed change first. Keep logtime and task-state changes separate unless the user explicitly requests both.
- After every mutation, verify through the corresponding read API. If saving succeeded but verification failed, report `saved: true`, `verified: false`; do not retry automatically.
- Do not expose passwords, cookies, OIDC fields, tokens, raw callback HTML, or credential-like task-comment content.

## Workflows

- For listing tasks/comments/logtimes and preparing or submitting a daily batch, read [references/logtime.md](references/logtime.md).
- For editing existing logtime, status transitions, or percent done, read [references/task-state.md](references/task-state.md).
- Read [references/api-flow.md](references/api-flow.md) before changing endpoints, payloads, form-contract handling, or debugging unexpected BecaWork responses.

## Resources

- Run `scripts/beca_logtime.py` for deterministic API calls and payload construction.
- Run `scripts/test_beca_logtime.py` for local unit tests. They must not call the live BecaWork service.
