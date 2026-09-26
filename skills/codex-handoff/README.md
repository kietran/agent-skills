# Claude Code → Codex handoff

This personal Claude Code skill lets Claude write a feature spec, invoke the local Codex CLI, watch its progress, and review the resulting code. Codex runs with the sign-in configured for the local `codex` CLI.

## Requirements

- Claude Code (VS Code extension or CLI) on the target machine
- Python 3.10 or newer, Git, and Codex CLI available on `PATH`
- A Git repository for each feature run
- A working Codex CLI login (`codex login status`); to use your ChatGPT subscription, sign in with ChatGPT rather than an OpenAI API key
- Optional: VS Code `code` command for automatically opening the live transcript. On a headless server, Claude reports its path so you can open it over Remote SSH.

## Install on another machine

Extract the archive into your personal Claude Code skills directory so the final paths are `~/.claude/skills/codex-handoff/SKILL.md` and `~/.claude/skills/codex-handoff/scripts/delegate.py`. Ensure the script is executable:

```sh
unzip -o codex-handoff-v5.zip -d ~/.claude/skills/
chmod +x ~/.claude/skills/codex-handoff/scripts/delegate.py
codex login status
```

If Codex is not signed in, run `codex login`. On a headless server, `codex login --device-auth` may be more convenient where device authentication is enabled for your ChatGPT account.

Start a new Claude Code session, then ask Claude to use `/codex-handoff` after you agree on a feature spec. The skill is personal, so it is available in every local repository on that machine.

## What you can inspect

The helper creates `<repo>/.codex-handoff/index.md` and a run directory under `<repo>/.codex-handoff/runs/`. Each run has a live `transcript.md` with the spec, Codex messages, commands, and file changes, plus `events.jsonl`, `progress.log`, `status.json`, and Codex's final response. The script tries to open the transcript in VS Code when `code` is available. Claude should include a workspace-relative Markdown link to the transcript in chat; if the extension does not render it as clickable, open the same path from VS Code Explorer. The helper adds `/.codex-handoff/` only to the local Git exclude file, leaving tracked files unchanged.

This is a view of the Codex CLI run, not a task in the Codex VS Code extension's native history. After completion, use `codex resume <thread-id>` for the native CLI transcript. The Claude handoff and completion behavior from the first version are unchanged.

## GPU or other host access

The default is Codex `workspace-write`. For a specific run that the user has authorized to access host GPU devices or other resources outside the Codex sandbox, add `--full-access`:

```sh
~/.claude/skills/codex-handoff/scripts/delegate.py start --repo /absolute/repo --spec /absolute/spec.md --model gpt-6-sol --full-access
```

The helper maps this to Codex's `--dangerously-bypass-approvals-and-sandbox` flag. It removes Codex's sandbox and approval prompts for that invocation, including on `resume` when the flag is provided there. It does not change the global Codex configuration, and the default remains `workspace-write`. If the GPU is still unavailable, check that the server or container exposes the device to the process; bypassing Codex's sandbox cannot create a missing device.

## Reasoning effort

Choose the Codex implementer's effort per run with `--reasoning-effort`. Omit it to keep the model's default. For Codex subagents spawned inside that run, use the independent `--subagent-reasoning-effort` option, optionally with `--subagent-model`:

```sh
~/.claude/skills/codex-handoff/scripts/delegate.py start \
  --repo /absolute/repo --spec /absolute/spec.md \
  --model gpt-6-sol --reasoning-effort high \
  --subagent-model gpt-6-luna --subagent-reasoning-effort medium
```

Supported values are `low`, `medium`, `high`, `xhigh`, `max`, and `ultra` where the selected model supports them. GPT-6 Luna does not support `ultra`. Pass the chosen options again when using `--resume`; each invocation records them in its `status.json` and transcript. The helper forwards them as Codex configuration overrides, so it does not change your global Codex defaults.
