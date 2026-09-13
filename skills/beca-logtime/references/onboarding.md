# Onboarding and authentication

## First-use flow

1. Run the requested command normally. The CLI readiness gate continues immediately when authentication is available.
2. On `SETUP_REQUIRED` or `requiredAction: OPEN_INTERACTIVE_TERMINAL`, use the exact original command in the error hint. Start it with `tty: true`, then expose the terminal panel using the returned `sessionId`. Do not run standalone `setup` for a pending `whoami`, `list-tasks`, `list-logtimes`, preview, or mutation request.
3. Stay attached to that terminal session until it succeeds or the user cancels. Do not open a blank terminal, ask the user to type a command manually, or ask them to send “xong” while the process is detached.
4. Report the verified name/email, active-task count, API-contract result, and credential-storage result.
5. Continue the original read or preview request through the CLI. A mutating request still requires its normal preview and explicit confirmation.

Browser automation and Computer Use are not authentication fallbacks for this skill. If no interactive terminal is available, show the exact setup command and wait for the user; if MFA/CAPTCHA is detected, report the limitation without switching tools.

Useful commands:

```text
python beca_logtime.py setup
python beca_logtime.py setup --username USER
python beca_logtime.py doctor
python beca_logtime.py whoami
python beca_logtime.py reset-auth
python beca_logtime.py version
```

Resolve `python` and the script path for the current OS; these examples are illustrative.

On Windows, prefer the absolute command emitted by the CLI, typically using the current `python.exe` or `py -3`. Do not translate the login flow into browser steps.

## Credentials and dependency bootstrap

Credential precedence is explicit cookie, environment variables, config username plus Python keyring, legacy Linux Secret Service/KWallet, then a secure terminal prompt.

- Store only username and non-secret setup metadata in the platform config directory.
- On first setup, automatically install the supported Python `keyring` package into `<config-dir>/python-packages`; do not modify system Python and do not ask the user to choose session-only authentication.
- Require a viable Windows Credential Manager, macOS Keychain, or Linux keyring backend before requesting credentials. If installation or the backend fails, stop with an actionable error instead of claiming setup completed.
- After successful SSO validation, save the password to the OS credential store automatically. A successful setup must be reusable by a new Python process.
- `reset-auth` removes only config and keyring entries created by this skill. It cannot remove environment variables.

## Platform config paths

- Windows: `%APPDATA%\beca-logtime\config.json`
- macOS: `~/Library/Application Support/beca-logtime/config.json`
- Linux: `$XDG_CONFIG_HOME/beca-logtime/config.json`, otherwise `~/.config/beca-logtime/config.json`

## Authentication boundary

Version 2 supports username/password SSO. Detect MFA, CAPTCHA, or verification challenges and return `AUTH_CHALLENGE_REQUIRED`; do not attempt to bypass or automate them. Validate successful authentication with `Work_GetInfLogin` before saving local setup state.
