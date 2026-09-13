# Onboarding and authentication

## First-use flow

1. Run the requested command normally. The CLI readiness gate continues immediately when authentication is available.
2. On `SETUP_REQUIRED`, `SETUP_REQUIRES_TTY`, or `requiredAction: OPEN_INTERACTIVE_TERMINAL`, use the exact command in the error hint. Start it in a PTY-backed, user-visible terminal and expose that terminal panel so the user can type into it.
3. Stay with the setup flow until it succeeds or the user cancels. Do not answer with setup instructions alone when a terminal tool is available.
4. Report the verified name/email, active-task count, API-contract result, and credential-storage result.
5. Continue the original read or preview request through the CLI. A mutating request still requires its normal preview and explicit confirmation.

Browser automation and Computer Use are not authentication fallbacks for this skill. If no interactive terminal is available, show the exact setup command and wait for the user; if MFA/CAPTCHA is detected, report the limitation without switching tools.

Useful commands:

```text
python beca_logtime.py setup
python beca_logtime.py setup --username USER
python beca_logtime.py setup --no-store
python beca_logtime.py doctor
python beca_logtime.py whoami
python beca_logtime.py reset-auth
python beca_logtime.py version
```

Resolve `python` and the script path for the current OS; these examples are illustrative.

On Windows, prefer the absolute command emitted by the CLI, typically using the current `python.exe` or `py -3`. Do not translate the login flow into browser steps.

## Credentials

Credential precedence is explicit cookie, environment variables, config username plus Python keyring, legacy Linux Secret Service/KWallet, then a secure terminal prompt.

- Store only username and non-secret setup metadata in the platform config directory.
- Use optional Python `keyring` for Windows Credential Manager, macOS Keychain, or a viable Linux keyring backend.
- If `keyring` is unavailable, explain that the password will last only for the current process. Ask permission before installing `keyring`; never install it silently.
- `reset-auth` removes only config and keyring entries created by this skill. It cannot remove environment variables.

## Platform config paths

- Windows: `%APPDATA%\beca-logtime\config.json`
- macOS: `~/Library/Application Support/beca-logtime/config.json`
- Linux: `$XDG_CONFIG_HOME/beca-logtime/config.json`, otherwise `~/.config/beca-logtime/config.json`

## Authentication boundary

Version 2 supports username/password SSO. Detect MFA, CAPTCHA, or verification challenges and return `AUTH_CHALLENGE_REQUIRED`; do not attempt to bypass or automate them. Validate successful authentication with `Work_GetInfLogin` before saving local setup state.
