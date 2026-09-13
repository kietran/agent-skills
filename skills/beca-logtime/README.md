# BecaWork Logtime

An Agent Skill for setting up and managing BecaWork logtime on Windows, macOS, and Linux.

## Install with Codex

Send this message to Codex:

> Install this skill from https://github.com/kietran/agent-skills/tree/main/skills/beca-logtime, then set up and verify BecaWork without changing any data.

Requirements: Codex and Python 3.11 or newer. First-time setup installs `keyring` into a skill-managed directory and stores the validated password in the operating-system credential store; the skill never stores passwords in its JSON config.

After installation, you can simply ask:

- “Xem task BecaWork của tôi.”
- “Kiểm tra logtime hôm qua.”
- “Chuẩn bị logtime hôm qua, chưa submit.”
- “Logtime gần nhất của tôi là ngày nào?”

On first use, the skill automatically starts read-only setup. It previews every mutation and requires explicit confirmation before changing BecaWork.

Authentication always runs through a user-visible terminal so the password can be entered securely. On Windows, `login --window` opens a dedicated console and waits for setup to finish instead of relying on a Codex terminal panel. Rejected credentials can be retried in the same window; the Agent resumes only after success and close confirmation, or after the user cancels. The Agent must not substitute browser automation or Computer Use for the skill's CLI.

If setup fails, ask Codex: “Kiểm tra BecaWork giúp tôi.” The `doctor` command reports Python, platform, network, credentials, login, and API-contract status without modifying data.
