---
name: beca-logtime
description: Automate BecaWork work-log discovery, preview, validation, confirmed submission, and confirmed edits. Use when the user wants to list BecaWork tasks, inspect BecaWork timesheet/logtime state, prepare yesterday's logtime, validate over-logtime rules, submit BecaWork logtime, or edit an existing BecaWork logtime row through work.becawork.vn APIs.
---

# BecaWork Logtime

Use this skill to work with BecaWork logtime safely. Default to previewing and validating before submitting. Never submit logtime without explicit user confirmation in the current turn.

## Quick Start

Prefer the bundled CLI:

```bash
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py list-tasks
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py list-logtimes --date 2026-07-01 --json
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py get-logtime --log-id 20997
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py preview --project-id 1330830 --work-id 1415256 --description "Worked on Public Wifi backend"
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py submit --project-id 1330830 --work-id 1415256 --description "Worked on Public Wifi backend"
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py preview-update --log-id 20997 --hours 6
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py update --log-id 20997 --hours 6
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py verify-logtime --work-id 1414089 --date 2026-06-30 --hours 2 --description "Vẽ diagram cho toàn bộ retrain pipeline"
```

Authenticate with `BECA_COOKIE` when available. If no cookie is present, let the script prompt for username/password; do not hardcode credentials in prompts, skill files, or scripts.

Default command output is human-readable review text. Use `--json` only for debugging, tests, or machine-readable automation output.

## Workflow

1. List candidate tasks with `list-tasks` when the work id or project id is unknown.
2. Run `preview` with `--project-id`, `--work-id`, `--description`, and optional `--date`, `--hours`, `--action`.
3. Inspect the text preview output:
   - `overLogtime.remainingAfterEntry` must be zero or positive.
   - `duplicates` must be empty unless the user explicitly wants a duplicate entry and passes `--allow-duplicate`.
   - The save endpoint must use the form id and step id returned by `Work_GetApiSetting`.
4. Run `submit` only when the user explicitly confirms saving. The script asks for `y` before POSTing unless `--yes` is passed intentionally by the user.
5. After POST, inspect the compact result. `verified` must be `true`; if it is `false`, the script reports `saved: true` and returns non-zero so the row can be checked manually.

## Edit Workflow

1. Use `list-logtimes` or `get-logtime --log-id` to identify the existing row.
2. Run `preview-update --log-id`, passing only fields that should change: optional `--date`, `--hours`, `--action`, `--description`.
3. Inspect the text preview output:
   - `logUserWorkflowId` is the row workflow id used for update; it is not the database `logId` and not the work id.
   - `validation.oldVal` must match the previous hours when the date is unchanged, or `0` when the date changes.
   - `duplicates` must be empty unless the user explicitly wants another row for the same work/date and passes `--allow-duplicate`.
4. Run `update` only when the user explicitly confirms saving. The script asks for `y` before PUTing unless `--yes` is passed intentionally by the user.
5. After PUT, inspect the compact result. `verified` must be `true`; update verification matches `logUserWorkflowId` because the database `logId` can change after edit.

Update v1 mirrors the web UI: preserve project and task (`Duan`, `Congviec`), allow only log date, hours, action, and description to change, block check-in rows, and block edits to rows owned by another email.

## Defaults

- `--date`: previous business day in `Asia/Ho_Chi_Minh`.
- `--hours`: `8`.
- `--action`: `Thực hiện`.
- `--description`: required, although the web UI marks `Mota` optional.
- `preview-update` and `update` preserve existing field values when optional update arguments are omitted.
- `submit` and `update` automatically verify the saved row through `Work_GetLogtimeByWorkId`.
- User-facing preview and post-save verification should be text summaries, not JSON. Keep JSON for `--json` output only.
- `UserId`: preserve values already prefixed with `P:`; otherwise prefix the numeric id with `P:`.

## Resources

- Read `references/api-flow.md` before changing endpoint behavior or debugging BecaWork API responses.
- Use `scripts/beca_logtime.py` for live calls and payload construction.
- Use `scripts/test_beca_logtime.py` for local unit tests that do not call BecaWork.
