# Onboarding and authentication

## First-use flow

1. Run the requested command normally. The CLI readiness gate continues immediately when authentication is available.
2. On `SETUP_REQUIRED`, run `setup` in an interactive terminal. Password entry must happen through the terminal's hidden prompt, never through chat or a command argument.
3. Report the verified name/email, active-task count, API-contract result, and credential-storage result.
4. Continue the original read or preview request. A mutating request still requires its normal preview and explicit confirmation.

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
