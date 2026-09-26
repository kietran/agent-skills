---
name: codex-handoff
description: Hand off an approved feature spec from Claude Code to the local Codex CLI, show live progress in VS Code, then review the implementation.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/delegate.py *)
---

# Codex handoff

Use this when the user asks you to implement a feature with Codex, delegate to Codex, or hand off a completed plan. You own the product direction and review. Codex owns the implementation.

1. Finish the feature spec before delegating. Include goal, current project context, exact scope, acceptance criteria, relevant files, constraints, tests, and what Codex should report. Resolve open product decisions with the user first.
2. Write the spec to a temporary UTF-8 file. Use the active repository root as `--repo` (`git rev-parse --show-toplevel`). Do not pass spec text as shell command arguments.
3. Start one Codex run per independent feature:

   `${CLAUDE_SKILL_DIR}/scripts/delegate.py start --repo "<absolute-repo-root>" --spec "<absolute-spec-file>" --model gpt-6-sol`

   If the user chooses a reasoning level for the Codex implementer, add `--reasoning-effort <low|medium|high|xhigh|max|ultra>`. Omit it to use the selected model's default. If Codex may spawn its own subagents and the user wants a separate default for them, add `--subagent-model <gpt-6-sol|gpt-6-luna>` and/or `--subagent-reasoning-effort <level>`. Luna supports up to `max`, not `ultra`. These options apply only to this Codex invocation and are recorded in `status.json` and the transcript.

   For a run that needs host GPU access or other resources blocked by the Codex sandbox, add `--full-access` **only when the user has authorized it for that work**. This opts that Codex invocation out of its sandbox and approval prompts; it does not grant GPU devices that the host/container has not exposed. The default remains `workspace-write`. When resuming the same Codex thread for GPU work, include `--full-access` on the resume invocation too.

   Use `gpt-6-luna` only for a narrowly scoped, straightforward change. The helper creates a live Markdown transcript under `<repo>/.codex-handoff/runs/`, opens it in VS Code when `code` is available, and returns `transcript_relative`, `transcript_md`, and `index_md`. Tell the user where to inspect the session. In the Claude chat, use `[Xem phiên Codex](<transcript_relative>)` and include the plain path as fallback. The workspace-relative link should be tried in the user's VS Code extension; do not claim it is clickable until the user confirms. The history index is `<repo>/.codex-handoff/index.md`.
4. Wait for completion with `${CLAUDE_SKILL_DIR}/scripts/delegate.py wait --run "<run-directory>"`. A single wait lasts at most 60 seconds; repeat it if the status remains `running`. Do not launch another writer against the same files while Codex is running.
5. After Codex finishes, read the final response and inspect the actual `git diff`, run relevant checks, and review against the acceptance criteria. If a correction is needed, write the review feedback to a new UTF-8 file and resume the exact Codex session:

   `${CLAUDE_SKILL_DIR}/scripts/delegate.py start --repo "<absolute-repo-root>" --spec "<absolute-feedback-file>" --resume "<thread-id>" --model gpt-6-sol`

   Reapply any selected `--reasoning-effort`, `--subagent-model`, and `--subagent-reasoning-effort` on a resume invocation. The helper explicitly passes them to Codex for each invocation rather than relying on the last run's settings.

   Add `--full-access` to this resume command when the follow-up still needs host GPU access and the user has authorized it. Each invocation records its selected `sandbox_mode` in `status.json` and the transcript.

6. Report what was implemented, what you verified, any remaining issues, and the Codex run log path. Do not claim the work passed review from Codex's summary alone.

The helper uses the local `codex exec` command and its existing ChatGPT sign-in. It does not need an OpenAI API key. Its run is a CLI Codex session; it does not create a task in the Codex VS Code extension's native history. The workspace transcript and index are the review surface for this bridge. They show the prompt, Codex messages, commands, and file-change events; raw JSONL events are also retained. `codex resume <thread-id>` can reopen the saved CLI conversation after completion. The helper only adds `/.codex-handoff/` to the repository's local Git exclude file, so generated history does not appear in `git status` or change tracked files.
