# Troubleshooting

Start with `doctor --json` so the Agent can interpret individual checks. Use `doctor --debug` only when redacted runtime metadata is useful.

| Code | Meaning | Next action |
|---|---|---|
| `PYTHON_UNSUPPORTED` | Python is older than 3.11 | Install Python 3.11+ and retry |
| `SETUP_REQUIRED` | No usable saved credential | Follow `OPEN_INTERACTIVE_TERMINAL` and rerun the exact original command from the hint in a PTY |
| `SETUP_REQUIRES_TTY` | Secure input is unavailable | Expose a PTY-backed terminal to the user; never switch to browser automation |
| `AUTH_INVALID` | Username/password was rejected | Re-enter credentials |
| `AUTH_CHALLENGE_REQUIRED` | MFA/CAPTCHA/verification detected | Complete login in the supported company flow; v2 does not automate it |
| `AUTH_FLOW_CHANGED` | SSO form/callback contract changed | Collect redacted diagnostics and update the parser |
| `AUTH_REQUIRED` | Cookie/session expired | Run `setup` again |
| `KEYRING_INSTALL_FAILED` | Managed keyring installation failed | Check Internet and bundled Python pip, then retry |
| `KEYRING_UNAVAILABLE` | Secure OS backend is unavailable | Repair the OS credential store; do not continue session-only |
| `NETWORK_ERROR` | DNS, VPN, proxy, or Internet problem | Restore connectivity and rerun `doctor` |
| `INVALID_RESPONSE` | API returned non-JSON/unexpected content | Check auth and BecaWork deployment status |

Every error must state whether BecaWork data changed. Default to `dataChanged: false`; mutation code sets it truthfully if a save may have happened. Do not include raw HTML, HTTP bodies, cookies, passwords, OIDC codes, state, or tokens in diagnostics.

For authentication failures, keep using this skill and its CLI. Computer Use is not a recovery route. `AUTH_CHALLENGE_REQUIRED` is a supported stop condition, not permission to automate MFA/CAPTCHA in a browser.

If the user sees a blank PowerShell prompt, the Agent opened the wrong terminal. Start the original CLI command with `tty: true` and reopen the terminal using that command's exact `sessionId`.
