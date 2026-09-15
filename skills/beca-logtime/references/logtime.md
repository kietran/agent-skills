# Tasks, comments, and daily logtime

Use `list-tasks` when a project/work id is unknown. Use `list-comments --since YYYY-MM-DD` for recent comments and preserve default credential-like redaction. Use `list-logtimes --date YYYY-MM-DD` for an exact calendar day. Use `latest-logtime --lookback-days 90` when the user asks for their most recent logtime; do not make the Agent invoke `list-logtimes` once per day. Weekend queries are skipped unless `--include-weekends` is explicitly needed.

For a normal day, prefer `daily`. Each entry is:

```text
TASK | HOURS | ĐÃ THỰC HIỆN | result=... | blockers=... | next=... | progress=VALUE
```

## Natural-language drafting flow

When the user asks to log time in natural language, build a review snapshot before invoking `preview`, `submit`, or `daily`.

1. Extract the task query, hours, and any stated description facts.
2. Classify description facts into the four live form sections:
   - `Đã thực hiện`: work that was performed.
   - `Kết quả`: an outcome or deliverable already achieved.
   - `Vướng mắc`: a stated blocker, dependency, risk, or unresolved issue.
   - `Bước tiếp theo`: a stated future action or follow-up.
3. Show all four sections in the snapshot. Render every missing value as `—`; never omit a row and never move unrelated facts into `Đã thực hiện` merely to avoid blanks.
4. If one or more description sections are missing, ask for all missing values in one short natural-language follow-up. Do not call the CLI yet. The user may provide them in one sentence or explicitly ask to leave them blank.
5. Merge the reply into the snapshot. When all fields are supplied, or the user explicitly accepts blank fields, run the normal CLI preview with `result=`, `blockers=`, and `next=` mapped to their respective sections. Submission still requires the existing explicit confirmation after CLI validation.

Use only facts stated by the user. Do not invent a successful result from an activity or assume `Vướng mắc: Không`; use `Không` only when the user says so. Do not repeat the same fact across multiple sections. For multiple tasks, maintain a separate snapshot for each task.

Example first response:

```text
Mình đã ghi nhận logtime:

- Task: Phân tích tài liệu nghiệp vụ
- Số giờ: 4 giờ
- Đã thực hiện: Hoàn thiện tài liệu OEE/OLE cho máy hàn và máy nén khí
- Kết quả: —
- Vướng mắc: —
- Bước tiếp theo: —

Bạn bổ sung giúp mình ba mục còn trống nhé. Có thể trả lời tự nhiên trong một câu.
```

- Resolve tasks against the fresh active-task list: work id, then exact/substring/fuzzy accent-insensitive title.
- Block ambiguous matches.
- Require whole hours allowed by live form metadata and exactly 8 total batch hours.
- Treat existing same-work/same-date rows as duplicates unless explicitly allowed.
- Preview the complete batch before one confirmation. Submit sequentially and stop on the first failure, reporting already-saved entries.
- Update optional progress only after that entry's logtime verifies successfully.

Defaults are the previous Vietnam business day, 8 hours, and action `Thực hiện`. New descriptions use the four live sections: `Đã thực hiện`, `Kết quả`, `Vướng mắc`, and `Bước tiếp theo`.
