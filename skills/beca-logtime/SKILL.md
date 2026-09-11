---
name: beca-logtime
description: Automate BecaWork daily logtime batches, task and new-comment discovery, preview, validation, confirmed submission, confirmed edits, and explicit task-state updates. Use when the user wants to log a full BecaWork day across one or more tasks, list BecaWork tasks or recent task comments, inspect BecaWork timesheet/logtime state, prepare yesterday's logtime, validate over-logtime rules, submit or edit BecaWork logtime, inspect a task, list next task statuses, update task status, or update task percent done through work.becawork.vn APIs.
---

# BecaWork Logtime

Use this skill to work with BecaWork logtime safely. Default to previewing and validating before submitting. Never submit logtime without explicit user confirmation in the current turn.

## Quick Start

Prefer the bundled CLI:

```bash
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py list-tasks
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py check-contracts
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py list-comments --since 2026-07-15
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py list-logtimes --date 2026-07-01 --json
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py get-logtime --log-id 20997
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py preview --project-id 1330830 --work-id 1415256 --description "Phát triển Public Wifi backend" --result "API hoạt động" --blockers "Không" --next-step "Kiểm thử thực tế"
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

## Task Comments

Use `list-comments --since YYYY-MM-DD` to scan active tasks assigned to the current user and return comments added or modified on or after that local calendar date. Use `--work-id` to inspect one task. The command is read-only and uses `Work_GetComment`; its output includes Work ID, comment author, timestamp, and plain-text comment content. Credential-like values are redacted by default; use `--show-sensitive` only when the user explicitly asks to reveal the original comment text.

For a weekday morning check, pass the same previous-business-day `target_date` used for logtime. On Monday this includes comments from Friday onward. Report a `Comment mới:` section only when rows are returned; do not invent comments from task descriptions or status history.

## Daily Workflow

Prefer `daily` for a normal workday. It fetches active personal tasks on every run, maps each task query to a current open task, enforces exactly 8 total hours, previews the whole batch once, then submits sequentially after one confirmation.

Entry format:

```bash
python ~/.codex/skills/beca-logtime/scripts/beca_logtime.py daily \
  --entry "task query | hours | Đã thực hiện | result=Kết quả | blockers=Vướng mắc | next=Bước tiếp theo" \
  --entry "task query | hours | Đã thực hiện | progress=28"
```

Rules:

- `task query` can be a work id or a close task title. Matching is work id first, then exact/substring/fuzzy title with Vietnamese accent-insensitive normalization.
- If matching is ambiguous or no task is close enough, block and show candidates. Do not auto-pick an ambiguous task.
- Each entry's hours must be a whole number from `1` through `16`, matching the live `SoGio` form metadata. Total hours must be exactly `8`; v1 has no override.
- The third field is required and maps to `Đã thực hiện`. Optional `result=`, `blockers=`, and `next=` fields populate the current four-section BecaWork description template.
- `progress=VALUE` is optional. When present, update `% done` only after that entry's logtime save has verified through the API.
- `daily` uses `--allow-duplicate` with the same semantics as `submit`: duplicates block by default.
- `daily --yes` may be used only when the user explicitly asks to submit without the interactive prompt.

If any entry fails after earlier entries have already saved, stop immediately, return non-zero, and report which entries were saved/verified and which entry failed.

## Workflow

1. List candidate tasks with `list-tasks` when the work id or project id is unknown.
2. Run `preview` with `--project-id`, `--work-id`, `--description`, and optional `--date`, `--hours`, `--action`.
   - Treat `--description` as `Đã thực hiện`; optionally pass `--result`, `--blockers`, and `--next-step`.
3. Inspect the text preview output:
   - `overLogtime.remainingAfterEntry` must be zero or positive.
   - `duplicates` must be empty unless the user explicitly wants a duplicate entry and passes `--allow-duplicate`.
   - The save endpoint must use the form id and step id returned by `Work_GetApiSetting`.
   - `SoGio` must match the live minimum, maximum, and decimal-place metadata; `Hanhdong` must match a current `selectItems[].value`.
4. Run `submit` only when the user explicitly confirms saving. The script asks for `y` before POSTing unless `--yes` is passed intentionally by the user.
5. After POST, inspect the compact result. `verified` must be `true`; if it is `false`, the script reports `saved: true` and returns non-zero so the row can be checked manually.

## Edit Workflow

1. Use `list-logtimes` or `get-logtime --log-id` to identify the existing row.
2. Run `preview-update --log-id`, passing only fields that should change: optional `--date`, `--hours`, `--action`, `--description`, `--result`, `--blockers`, and `--next-step`. Pass `--description` whenever changing any structured description section.
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
- `--hours`: `8`; the current Backend form accepts whole numbers from `1` through `16` and rejects decimal values.
- `--action`: `Thực hiện`.
- `--description`: required for new rows and maps to `Đã thực hiện`; the CLI builds the four-section HTML template returned by `Mota.defaultValue`. Raw HTML remains accepted for legacy callers.
- `daily`: default date is previous business day, fetches active tasks with `rowNumber=200`, and requires exactly 8 total hours.
- `preview-update` and `update` preserve existing field values when optional update arguments are omitted.
- `submit`, `daily`, and `update` automatically verify saved rows through `Work_GetLogtimeByWorkId`.
- `update-status` verifies status through `Work_DetailInfo.status/statusName`.
- `update-progress` verifies percent done through `Work_DetailInfo.progress`.
- User-facing preview and post-save verification should be text summaries, not JSON. Keep JSON for `--json` output only.
- `UserId`: preserve values already prefixed with `P:`; otherwise prefix the numeric id with `P:`.
- `list-logtimes --date`: filter the period-style Backend response to the exact requested calendar date.
- Run `check-contracts` before live mutations after a suspected Backend deployment. It reads form metadata only and fails when required fields or supported constraints drift.

## Resources

- Read `references/api-flow.md` before changing endpoint behavior or debugging BecaWork API responses.
- Use `scripts/beca_logtime.py` for live calls and payload construction.
- Use `scripts/test_beca_logtime.py` for local unit tests that do not call BecaWork.
