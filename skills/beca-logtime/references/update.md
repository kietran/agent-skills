# Updating this skill

Use this workflow only after the user explicitly asks to update the installed `beca-logtime` skill. The user can simply say: `Cập nhật skill beca-logtime lên bản mới nhất.`

## Canonical source

- Repository: `kietran/agent-skills`
- Ref: `beca-logtime-v2`
- Skill path: `skills/beca-logtime`
- Installed folder name: `beca-logtime`

Treat these values as the complete source specification; the user does not need to repeat the GitHub URL.

## User-data boundary

An update may replace only the installed `beca-logtime` skill directory. BecaWork user state lives outside that directory:

- Windows: `%APPDATA%\beca-logtime\config.json` and `%APPDATA%\beca-logtime\python-packages`
- macOS: `~/Library/Application Support/beca-logtime/config.json` and its sibling `python-packages`
- Linux: `$XDG_CONFIG_HOME/beca-logtime/config.json`, otherwise `~/.config/beca-logtime/config.json`, and its sibling `python-packages`
- Passwords: the operating-system credential store through Python keyring

Never delete, move, overwrite, recreate, or reset those locations during an update. Never run `reset-auth` or `setup` as part of updating. Do not request or expose the stored password. Logtime and task data remain on BecaWork and are not part of the local skill replacement.

## Safe update workflow

1. Resolve the current skill directory from this `SKILL.md`; do not assume a fixed home directory.
2. Inventory the current installed skill. Treat unexpected non-cache files as user-owned: preserve them in the code backup and do not silently discard or overwrite them.
3. Create a fresh temporary staging directory. Use the bundled `skill-installer` helper with the canonical repository, ref, and skill path above, targeting the staging directory rather than the live skills directory.
4. Before touching the installed copy, validate the staged skill with `skill-creator/scripts/quick_validate.py`, run its `scripts/test_beca_logtime.py`, and run `scripts/beca_logtime.py version`. If download or validation fails, leave the current installation untouched.
5. Record the current and staged versions. Do not downgrade unless the user explicitly requests it. The ref points to the latest supported v2 bundle; a same-version bundle may still contain documentation-only changes.
6. Move the current skill directory to a uniquely named code backup, then move the validated staged directory into the original exact location. Keep both moves on the same filesystem when possible. Do not target a home directory, skills root, config directory, or unresolved environment variable with a recursive operation.
7. Run the new local `version` command and the unit tests from the installed location. If either fails, restore the code backup immediately and report the failure.
8. After successful verification, remove only the temporary staging files and the old code backup. Never remove the user-data locations listed above.
9. Report the old and new versions, validation results, canonical source ref, and that user data was preserved. The updated skill is available on the next turn.

Use platform-appropriate file operations. On Windows, macOS, and Linux the update semantics are identical; only path syntax differs.
