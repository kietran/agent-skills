# Troubleshooting

Start with `doctor --json` so the Agent can interpret individual checks. Use `doctor --debug` only when redacted runtime metadata is useful.

| Code | Meaning | Next action |
|---|---|---|
| `PYTHON_UNSUPPORTED` | Python is older than 3.11 | Install Python 3.11+ and retry |
| `SETUP_REQUIRED` | No usable saved credential | On Windows run `login --window`; on macOS/Linux expose `setup` in a PTY |
| `SETUP_REQUIRES_TTY` | Current stdin cannot accept secure input | On Windows run `login --window`; otherwise expose a PTY-backed terminal |
| `LOGIN_WINDOW_FAILED` | Windows login console did not open or setup failed | Review the visible window; fall back to `setup` in a user-visible terminal |
| `LOGIN_WINDOW_UNSUPPORTED` | `--window` was used outside Windows | Run `setup` in a user-visible PTY |
| `AUTH_INVALID` | Username/password was rejected | The Windows login session stays open; press Enter to retry or `q` to cancel |
| `AUTH_CHALLENGE_REQUIRED` | MFA/CAPTCHA/verification detected | Complete login in the supported company flow; v2 does not automate it |
| `AUTH_FLOW_CHANGED` | SSO form/callback contract changed | Collect redacted diagnostics and update the parser |
| `AUTH_REQUIRED` | Cookie/session expired | Run `setup` again |
| `KEYRING_INSTALL_FAILED` | Managed keyring installation failed | Check Internet and bundled Python pip, then retry |
| `KEYRING_UNAVAILABLE` | Secure OS backend is unavailable | Repair the OS credential store; do not continue session-only |
| `NETWORK_ERROR` | DNS, VPN, proxy, or Internet problem | Restore connectivity and rerun `doctor` |
| `INVALID_RESPONSE` | API returned non-JSON/unexpected content | Check auth and BecaWork deployment status |

Every error must state whether BecaWork data changed. Default to `dataChanged: false`; mutation code sets it truthfully if a save may have happened. Do not include raw HTML, HTTP bodies, cookies, passwords, OIDC codes, state, or tokens in diagnostics.

For authentication failures, keep using this skill and its CLI. Computer Use is not a recovery route. `AUTH_CHALLENGE_REQUIRED` is a supported stop condition, not permission to automate MFA/CAPTCHA in a browser.

If a Windows user sees a blank PowerShell prompt, stop any hidden PTY and use `login --window`. It creates a separate console attached directly to the authentication process, so Codex panel state cannot redirect the user into an unrelated shell.

On macOS/Linux, if `open_in_codex` returns `status: queued`, the app has not displayed the PTY yet. Keep the same session alive and do not report the prompt as visible.
