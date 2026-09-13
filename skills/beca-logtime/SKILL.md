---
name: beca-logtime
description: Set up, troubleshoot, and use BecaWork logtime through the bundled CLI across Windows, macOS, and Linux. Use when the user mentions BecaWork setup or login, tasks, comments, timesheets, daily logtime, existing logtime edits, task status, or percent done. Authentication must use an interactive terminal, never browser automation or Computer Use.
---

# BecaWork Logtime

Use the bundled CLI for BecaWork operations. Keep command details out of the conversation unless they help diagnose a problem; users should be able to speak naturally.

## Mandatory authentication route

- Always use `scripts/beca_logtime.py` for BecaWork authentication and operations. Do not use a browser, browser automation, or Computer Use as an alternative implementation of this skill.
- When a normal BecaWork command returns `SETUP_REQUIRED` or `requiredAction: OPEN_INTERACTIVE_TERMINAL`, rerun that exact original command from the error hint in a PTY-backed terminal. Do not run standalone `setup`: the original command performs onboarding and then completes the user's request in the same Python process.
- On Codex Desktop, start the command with the terminal execution tool using `tty: true`, take the returned `sessionId`, and call `open_in_codex` for a terminal with that exact `sessionId`. Never open a blank terminal or claim it is waiting for input without verifying the process is still attached.
- Do not stop after reporting that login is missing, and do not ask the user to discover the setup command. Continue until setup exits successfully, the user cancels, or no interactive terminal capability exists.
- Wait on the same terminal session until the command completes. Do not ask the user to send “xong” and then launch a second Python process.
- If the host cannot expose an interactive terminal, give the user the exact command returned by the CLI and ask them to run it. Do not silently switch to browser automation.
- For `AUTH_CHALLENGE_REQUIRED`, explain that MFA/CAPTCHA is outside v2 support. Do not bypass the skill or continue the requested BecaWork operation through Computer Use.

## Runtime and first use

- Require Python 3.11 or newer. Locate the script relative to this `SKILL.md`; do not hardcode `~/.codex`. Prefer the active Python executable, then try `python3`, `python`, and Windows `py -3`.
- For any BecaWork request, let the CLI readiness gate detect missing setup. Never ask the user to paste a password into chat.
- First-time onboarding automatically installs the supported `keyring` dependency into the skill's managed config directory and stores the validated password in the operating-system credential store. Do not offer a session-only choice.
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
