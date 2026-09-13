# Onboarding and authentication

## First-use flow

1. Run the requested command normally. The CLI readiness gate continues immediately when authentication is available.
2. On Windows, if it returns `SETUP_REQUIRED` or `SETUP_REQUIRES_TTY`, run `login --window` through the normal terminal execution tool. The command creates a dedicated console for username/password input and blocks until that window exits; it does not depend on a Codex terminal tab.
3. Keep the entire interaction in that window. `AUTH_INVALID`, invalid blank input, and retryable network errors are shown there. After an error, Enter starts another attempt and `q` cancels. Each retry creates a fresh HTTP client and prompts for a fresh password instead of reusing a rejected credential.
4. After successful setup, the window waits for Enter before closing. The parent command returns only then. If the user chooses `q` or closes the window, treat login as cancelled and do not run an account check.
5. Rerun the original read or preview command only after exit code 0. Mutating requests still require their normal preview and explicit confirmation; login never resumes a pending mutation automatically.
6. Report the verified name/email, active-task count, API-contract result, and credential-storage result.

On macOS and Linux, use `setup` in a PTY-backed terminal and expose the exact returned session. Treat `status: queued` as not visible and keep the PTY alive while the panel is deferred. If no interactive terminal is available, show the exact setup command and wait for the user.

Browser automation and Computer Use are not authentication fallbacks for this skill. If no interactive terminal is available, show the exact setup command and wait for the user; if MFA/CAPTCHA is detected, report the limitation without switching tools.

Useful commands:

```text
python beca_logtime.py setup
python beca_logtime.py setup --username USER
python beca_logtime.py login --window
python beca_logtime.py doctor
python beca_logtime.py whoami
python beca_logtime.py reset-auth
python beca_logtime.py version
```

Resolve `python` and the script path for the current OS; these examples are illustrative.

On Windows, prefer the absolute command emitted by the CLI, typically using the current `python.exe` or `py -3`. `login --window` is the supported Codex Desktop route because it does not depend on `open_in_codex`. Do not translate the login flow into browser steps.

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
