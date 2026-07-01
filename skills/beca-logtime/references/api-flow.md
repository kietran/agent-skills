# BecaWork Logtime API Flow

This reference documents the observed BecaWork flow for `https://work.becawork.vn`.

## Discovery URLs

- Swagger UI: `https://work.becawork.vn/swagger`
- OpenAPI JSON: `https://work.becawork.vn/swagger/v1/swagger.json`
- Work pages:
  - `/work`
  - `/work/mywork`
  - `/work/timesheet`

## Authentication

Use `BECA_COOKIE` when supplied. For interactive login, start at SSO with:

- host: `https://sso.becawork.vn`
- client id: `vwork.work`
- redirect URI: `https://work.becawork.vn/signin-oidc`

POST/PUT/PATCH/DELETE requests to `work.becawork.vn` must include `X-XSRF-TOKEN` from:

```text
GET https://work.becawork.vn/api/antiforgery/token
```

## Task And Timesheet Endpoints

List current work:

```text
GET /api/Default/Work_GetWorkInProcess
```

Useful query defaults:

```text
title=
projectName=
group=
status=<10 spaces>
employee=
type=Xử lý
isComplete=1
layout=2
pageNumber=1
rowNumber=15
projectType=
typeSort=0
overDue=-1
```

Important response fields:

- `projectId`: may contain trailing spaces; strip before reuse.
- `projectName`
- `userWorkflowId`: use as logtime `Congviec` value.
- `title`
- `children`: nested works; flatten recursively when listing.

Load projects:

```text
GET /api/Default/Work_GetProjectByUser?type=&department=&userId=&name=&projectId=
```

Load work options for a selected project:

```text
GET /api/Default/Work_GetWorkInForm?projectId={projectId}
```

The response uses `value` as `userWorkflowId` and `label` as the task title.

Read timesheet:

```text
GET /api/Default/Work_TimeSheetReport?date={YYYY-MM-DD}&projectName=-1&email=P:{userId}&department={departmentId}
```

Observed page default on 2026-07-01:

```text
GET /api/Default/Work_TimeSheetReport?date=2026-07-01&projectName=-1&email=P:10000&department=5
```

Read list-view logtime rows:

```text
GET /api/Default/Work_TimeSheetPersonalLayoutList?date={YYYY-MM-DD}&projectId=-1&email=P:{userId}&department={departmentId}
```

Read one logtime row:

```text
GET /api/Default/Work_GetLogtimeById?id={logId}
```

Important logtime identifiers:

- `Id`: database logtime id used by `Work_GetLogtimeById`.
- `UserWorkflowId`: workflow row id of the logtime row; use this as `workId` in the update endpoint.
- `Congviec`: work/task id being logged; do not use this as the update row id.
- `Duan`: project id.

## Logtime Form

Load settings:

```text
GET /api/Default/Work_GetApiSetting
```

Use:

- `id_getWorkFormLogTime.id` as `formId` (observed `101`)
- `id_getWorkFormLogTimeStep.id` as `stepId` (observed `415`)

Load form format:

```text
POST /api/ApiEoffice/Eoffice_GetData?urlStr=getFormatWorkflow&projectId=undefined&workId=undefined
Content-Type: application/json; charset=UTF-8

[
  {"Name":"id","Value":101},
  {"Name":"isChild"},
  {"Name":"WorkType"}
]
```

Relevant fields:

- `Nguoilap`: default current full name, view-only
- `Ngaylap`: created date/time
- `Email`: current email, view-only
- `Duan`: required project id
- `Congviec`: required work id, filtered by `Duan`
- `Ngay`: required log date
- `SoGio`: required hours
- `Hanhdong`: required select, observed options `Thực hiện`, `Xem xét`, `Kiểm thử`, `Theo dõi`
- `Mota`: description as HTML
- `UserId`: current person id such as `P:10000`

## Validation

Check remaining hours before submit:

```text
GET /api/Default/Work_CheckOverInLogtime?newVal={hours}&oldVal=0&day={YYYY-MM-DD}&isCheckin=false
```

Observed behavior:

- Non-negative number means the entry is allowed.
- `0` means the day reaches the full daily limit after the new entry.
- Negative number means the entry must be blocked.
- For update, pass `oldVal={existing SoGio}` when the date is unchanged.
- For update with changed date, pass `oldVal=0`, matching the frontend `ModalLogTime` behavior.

Check duplicates for the selected work:

```text
GET /api/Default/Work_GetLogtimeByWorkId?WorkFlowId={workId}
```

Treat an existing row with the same normalized `Ngay` date as a duplicate by default.

When editing an existing row, exclude the current row by either `Id` or `UserWorkflowId` before treating same-work/same-day rows as duplicates.

## Save

Save endpoint:

```text
POST /api/ApiEoffice/Eoffice_ValidateAndInsertData?urlStr=/api/apikey/userWorkflows/{formId}/addDynamicUserWorkflow/{stepId}&apiId={formId}
Content-Type: application/json; charset=UTF-8
Origin: https://work.becawork.vn
Referer: https://work.becawork.vn/work/timesheet
X-XSRF-TOKEN: {token}
```

Payload shape:

```json
{
  "data": [
    {"name": "Nguoilap", "value": "Demo User"},
    {"name": "Ngaylap", "value": "2026-07-01 10:48:54"},
    {"name": "Email", "value": "user@example.com"},
    {"name": "Duan", "value": "1330830"},
    {"name": "Congviec", "value": "1415256"},
    {"name": "Ngay", "value": "2026-07-01 00:00:00"},
    {"name": "SoGio", "value": "8"},
    {"name": "Hanhdong", "value": "Thực hiện"},
    {"name": "Mota", "value": "<p>Worked on Public Wifi backend</p>"},
    {"name": "UserId", "value": "P:10000"}
  ],
  "data_json": "{\"Nguoilap\":\"...\"}",
  "isDraft": true
}
```

Always validate and preview before sending this POST.

CLI default output for preview and post-save verification is human-readable text for quick review. Use `--json` only when raw structured output is needed.

After a successful POST, verify through:

```text
GET /api/Default/Work_GetLogtimeByWorkId?WorkFlowId={workId}
```

For new rows, match by normalized `Ngay`, `SoGio`, `Mota`, current `Email`/`UserId` when present, and the submitted `Congviec`. If verification fails after POST, report `saved: true` and `verified: false`; do not silently claim success.

## Daily Batch

The daily command is a thin orchestration layer over the same read, validate, save, and verify endpoints. It does not use a persistent alias/cache as source of truth.

Task resolution:

1. Fetch active personal tasks with `Work_GetWorkInProcess`.
2. Use `mine_only=true` client-side filtering and `rowNumber=200`.
3. Resolve each entry task by exact `userWorkflowId` first.
4. If not a work id, match by title with lowercase, whitespace normalization, and Vietnamese accent removal.
5. Accept clear exact, substring, or fuzzy matches; block ambiguous/no-match cases and print top candidates.

Entry format:

```text
TASK | HOURS | DESCRIPTION
TASK | HOURS | DESCRIPTION | progress=VALUE
```

Batch validation before any save:

- Sum all entry hours and require exactly `8`.
- Call `Work_CheckOverInLogtime?newVal=8&oldVal=0&day={YYYY-MM-DD}&isCheckin=false`; block negative responses.
- Prepare each logtime payload with the same add flow and run per-task duplicate checks.
- Block duplicate work ids inside the same daily batch unless duplicate override was explicitly requested.

Daily submit order:

```text
GET Work_GetWorkInProcess
GET Work_CheckOverInLogtime(newVal=8, oldVal=0)
prepare each entry with add validation and duplicate checks
preview all entries once
confirm once
POST entry 1
GET Work_GetLogtimeByWorkId for entry 1 verification
optional PUT Work_UpdateJsonData for entry 1 progress
POST entry 2
GET Work_GetLogtimeByWorkId for entry 2 verification
...
```

If a later entry fails after earlier entries saved, stop immediately, exit non-zero, and report saved/verified entries plus the failed entry. Do not continue mutating subsequent entries.

Progress in daily:

- `progress=VALUE` uses the same normalization and endpoint as `update-progress`.
- Run progress update only after the corresponding logtime row has saved and verified.
- Verify with `Work_DetailInfo.progress`.

## Update

Frontend service mapping:

```text
DefaultService.updateFormJson(logUserWorkflowId, payload, formId)
```

Observed endpoint:

```text
PUT /api/ApiEoffice/Eoffice_UpdateData?urlStr=Work_UpdateFormJson&apiId={formId}&workId={logUserWorkflowId}
Content-Type: application/json; charset=UTF-8
Origin: https://work.becawork.vn
Referer: https://work.becawork.vn/work/timesheet
X-XSRF-TOKEN: {token}
```

Payload shape is the same `data`, `data_json`, `isDraft` object used for add. For edit v1, preserve existing `Duan`, `Congviec`, `Nguoilap`, `Email`, and `UserId`; override only:

- `Ngay`
- `SoGio`
- `Hanhdong`
- `Mota`

Block update when:

- current login email does not match the row `Email`
- the row has `CheckinId`
- `Work_CheckOverInLogtime` returns a negative value
- another row for the same work/date exists and duplicate override was not explicitly requested

After a successful PUT, verify through:

```text
GET /api/Default/Work_GetLogtimeByWorkId?WorkFlowId={workId}
```

For edited rows, match by `UserWorkflowId` plus normalized `Ngay`, `SoGio`, and `Mota`. Do not rely on the old database `Id`; BecaWork can assign a new `Id` after update while preserving `UserWorkflowId`.

## Task Status And Progress

Task-state updates are separate from logtime. Never update status or percent done as an automatic side effect of submitting logtime.

Read task detail:

```text
GET /api/Default/Work_DetailInfo?workId={workId}
```

Important fields:

- `userWorkflowId`: task/work id.
- `projectId`, `projectName`
- `status`: current status workflow id, for example `1330831`.
- `statusName`: display name, for example `Open`.
- `statusType`: status category, for example `Cơ bản`.
- `progress`: percent done text, for example `28%`, `70%`, or `80`.

List valid status transitions:

```text
GET /api/Default/Work_GetNextStatus?WorkId={workId}&projectId={projectId}&useForChild=false
```

Important status identifiers:

- `id`: internal status row id, for example `9213`; do not use this for update.
- `userWorkflowId`: status workflow id, for example `1330833`; use this as `statusId` in `Work_UpdateStatusWork`.
- `isCurrentStatus`: marks the current task status.
- `trangThaiCongViec`: status category; values such as `Hủy` should be blocked by default.

Preflight status update:

```text
GET /api/Default/Work_CheckRuleUpdateProcessWork?workId={workId}
GET /api/Default/Work_CheckBeforeSaveChangStatusWithFormExtendInfo?workId={workId}
GET /api/Default/Work_CheckBeforeSaveChangStatus?workId={workId}&statusId={statusUserWorkflowId}
```

Treat empty string, `None`, and `null` as pass. Treat any other response text as a blocking validation message.

Update status:

```text
GET /api/Default/Work_UpdateStatusWork?workId={workId}&statusId={statusUserWorkflowId}
```

This is a mutating GET used by the frontend. The CLI must preview, validate, and require explicit confirmation before calling it. Verify by re-reading `Work_DetailInfo` and matching `status` or `statusName`.

Update percent done:

```text
PUT /api/Default/Work_UpdateJsonData?userWorkFlowId={workId}&fileName=Tiendo&value={percent}%
Content-Type: application/json; charset=UTF-8
Origin: https://work.becawork.vn
Referer: https://work.becawork.vn/work/timesheet
X-XSRF-TOKEN: {token}
```

Normalize user input:

- `28` -> `28%`
- `28%` -> `28%`
- `28.5` -> `28.5%`

Reject values below `0` or above `100`. Verify by re-reading `Work_DetailInfo.progress` and comparing normalized numeric values.
