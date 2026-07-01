---
name: beca-logtime
description: Automate BecaWork daily logtime batches, work-log discovery, preview, validation, confirmed submission, confirmed edits, and explicit task-state updates. Use when the user wants to log a full BecaWork day across one or more tasks, list BecaWork tasks, inspect BecaWork timesheet/logtime state, prepare yesterday's logtime, validate over-logtime rules, submit or edit BecaWork logtime, inspect a task, list next task statuses, update task status, or update task percent done through work.becawork.vn APIs.
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
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py daily --entry "Phát triển trang Multimedia | 6 | Design lại phần điều khiển trên FE" --entry "Lên diagram flow training AI | 2 | Vẽ diagram cho toàn bộ retrain pipeline | progress=28"
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py preview-update --log-id 20997 --hours 6
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py update --log-id 20997 --hours 6
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py verify-logtime --work-id 1414089 --date 2026-06-30 --hours 2 --description "Vẽ diagram cho toàn bộ retrain pipeline"
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py get-task --work-id 1415256
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py list-statuses --work-id 1415256
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py preview-status --work-id 1415256 --status "In Progress"
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py update-status --work-id 1415256 --status "In Progress"
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py preview-progress --work-id 1415256 --progress 28
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py update-progress --work-id 1415256 --progress 28
```

Authenticate with `BECA_COOKIE` when available. If no cookie is present, let the script prompt for username/password; do not hardcode credentials in prompts, skill files, or scripts.

Default command output is human-readable review text. Use `--json` only for debugging, tests, or machine-readable automation output.

## Daily Workflow

Prefer `daily` for a normal workday. It fetches active personal tasks on every run, maps each task query to a current open task, enforces exactly 8 total hours, previews the whole batch once, then submits sequentially after one confirmation.

Entry format:

```bash
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py daily \
  --entry "task query | hours | description" \
  --entry "task query | hours | description | progress=28"
```

Rules:

- `task query` can be a work id or a close task title. Matching is work id first, then exact/substring/fuzzy title with Vietnamese accent-insensitive normalization.
- If matching is ambiguous or no task is close enough, block and show candidates. Do not auto-pick an ambiguous task.
- Total hours must be exactly `8`; v1 has no override.
- `description` is required for every entry.
- `progress=VALUE` is optional. When present, update `% done` only after that entry's logtime save has verified through the API.
- `daily` uses `--allow-duplicate` with the same semantics as `submit`: duplicates block by default.
- `daily --yes` may be used only when the user explicitly asks to submit without the interactive prompt.

If any entry fails after earlier entries have already saved, stop immediately, return non-zero, and report which entries were saved/verified and which entry failed.

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

## Task State Workflow

Task status and percent-done updates are separate from daily logtime. Do not change task state as a side effect of submitting logtime.

1. Use `get-task --work-id` to inspect the current task title, project, status, and progress.
2. Use `list-statuses --work-id` before status changes. Update only to statuses returned by `Work_GetNextStatus`.
3. Run `preview-status --work-id ... --status ...` or `--status-id ...`; inspect the text preview and validation results.
4. Run `update-status` only when the user explicitly confirms the status change. The script asks for `UPDATE` before calling the mutating endpoint unless `--yes` is intentionally passed by the user.
5. Run `preview-progress --work-id ... --progress ...` before changing `% done`.
6. Run `update-progress` only after explicit confirmation. The script normalizes `28`, `28%`, and `28.5` to BecaWork percent text and verifies with `Work_DetailInfo.progress`.

Status safety:

- Use status `userWorkflowId` as the update `statusId`; do not use the internal `id`.
- Block `Pending`, `Reject`, and `trangThaiCongViec = Hủy` by default; use `--allow-cancel` only when the user explicitly requests a cancel/reject transition.
- Run task update guards and block when BecaWork returns a non-empty validation message.

## Defaults

- `--date`: previous business day in `Asia/Ho_Chi_Minh`.
- `--hours`: `8`.
- `--action`: `Thực hiện`.
- `--description`: required, although the web UI marks `Mota` optional.
- `daily`: default date is previous business day, fetches active tasks with `rowNumber=200`, and requires exactly 8 total hours.
- `preview-update` and `update` preserve existing field values when optional update arguments are omitted.
- `submit`, `daily`, and `update` automatically verify saved rows through `Work_GetLogtimeByWorkId`.
- `update-status` verifies status through `Work_DetailInfo.status/statusName`.
- `update-progress` verifies percent done through `Work_DetailInfo.progress`.
- User-facing preview and post-save verification should be text summaries, not JSON. Keep JSON for `--json` output only.
- `UserId`: preserve values already prefixed with `P:`; otherwise prefix the numeric id with `P:`.

## Resources

- Read `references/api-flow.md` before changing endpoint behavior or debugging BecaWork API responses.
- Use `scripts/beca_logtime.py` for live calls and payload construction.
- Use `scripts/test_beca_logtime.py` for local unit tests that do not call BecaWork.
