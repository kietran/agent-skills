# Tasks, comments, and daily logtime

Use `list-tasks` when a project/work id is unknown. Use `list-comments --since YYYY-MM-DD` for recent comments and preserve default credential-like redaction. Use `list-logtimes --date YYYY-MM-DD` for an exact calendar day. Use `latest-logtime --lookback-days 90` when the user asks for their most recent logtime; do not make the Agent invoke `list-logtimes` once per day. Weekend queries are skipped unless `--include-weekends` is explicitly needed.

For a normal day, prefer `daily`. Each entry is:

```text
TASK | HOURS | ĐÃ THỰC HIỆN | result=... | blockers=... | next=... | progress=VALUE
```

- Resolve tasks against the fresh active-task list: work id, then exact/substring/fuzzy accent-insensitive title.
- Block ambiguous matches.
- Require whole hours allowed by live form metadata and exactly 8 total batch hours.
- Treat existing same-work/same-date rows as duplicates unless explicitly allowed.
- Preview the complete batch before one confirmation. Submit sequentially and stop on the first failure, reporting already-saved entries.
- Update optional progress only after that entry's logtime verifies successfully.

Defaults are the previous Vietnam business day, 8 hours, and action `Thực hiện`. New descriptions use the four live sections: `Đã thực hiện`, `Kết quả`, `Vướng mắc`, and `Bước tiếp theo`.
