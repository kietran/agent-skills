# BecaWork Logtime

An Agent Skill for setting up and managing BecaWork logtime on Windows, macOS, and Linux.

## Install with Codex

Send this message to Codex:

> Install this skill from https://github.com/kietran/agent-skills/tree/main/skills/beca-logtime, then set up and verify BecaWork without changing any data.

Requirements: Codex and Python 3.11 or newer. Secure password storage is optional through Python `keyring`; the skill never stores passwords in its JSON config.

After installation, you can simply ask:

- “Xem task BecaWork của tôi.”
- “Kiểm tra logtime hôm qua.”
- “Chuẩn bị logtime hôm qua, chưa submit.”

On first use, the skill automatically starts read-only setup. It previews every mutation and requires explicit confirmation before changing BecaWork.

If setup fails, ask Codex: “Kiểm tra BecaWork giúp tôi.” The `doctor` command reports Python, platform, network, credentials, login, and API-contract status without modifying data.
