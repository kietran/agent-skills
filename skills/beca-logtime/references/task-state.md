# Existing logtime and task state

## Edit logtime

1. Identify the row with `list-logtimes` or `get-logtime --log-id`.
2. Run `preview-update`, passing only changed values.
3. Preserve project/task fields, block check-in rows and rows owned by another email, and exclude the current row from duplicate detection.
4. Run `update` only after explicit confirmation, then verify by log `UserWorkflowId`.

## Task status

1. Inspect with `get-task` and fetch allowed transitions with `list-statuses`.
2. Preview with `preview-status`.
3. Use status `userWorkflowId`, never its internal id.
4. Block Pending/Reject/cancel transitions unless explicitly requested with `--allow-cancel`.
5. Update only after confirmation and verify the returned task status.

## Percent done

Preview with `preview-progress`, accept values such as `28`, `28%`, or `28.5`, and update only after confirmation. Verify through `Work_DetailInfo.progress`. Never change status or percent done merely as a side effect of logging time.
