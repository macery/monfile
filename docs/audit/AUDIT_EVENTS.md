# Audit Event Catalog

Every event is sent to syslog/journald with ident `monfile` in key=value format:

```
monfile[1234]: event=ACTION_OK folder=inbound-a path="/data/inbound/a/report.pdf" action=copy index=0 dest="/data/outbound/a/report.pdf" sha256=9f86d0… bytes=12345
```

## Formatting rules
- `event` is always the first key.
- `folder` and `path` are present whenever they apply.
- Path values are double-quoted. Control characters, quotes, and backslashes inside them are escaped (`\n`, `\"`, `\\`, `\xNN`).
- Timestamps come from syslog/journald. Manifests carry their own UTC ISO-8601 timestamps.
- **Change control:** code and this catalog must change together (AGENTS.md rule 6). Event IDs are stable, and a retired ID is never reused.

| Event ID | Severity | When | Additional keys |
|---|---|---|---|
| `SERVICE_START` | info | Process started (any mode) | `version`, `mode` (run/scan), `pid`, `user`, `python`, `fips` (true/false), `dryrun` (true/false) |
| `SERVICE_STOP` | info | Clean shutdown | `reason` (signal/once-complete) |
| `CONFIG_LOADED` | info | Config parsed and validated | `config`, `sha256`, `folders` (enabled), `disabled` |
| `CONFIG_INVALID` | error | Validation failure (process exits) | `config`, `error` |
| `CONFIG_PERMS_INSECURE` | error | Config ownership/mode check failed (process exits) | `config`, `owner`, `mode` |
| `LOCK_BUSY` | info | Another instance holds the lock (scan mode exits 0, run mode exits 1) | `lock` |
| `FOLDER_DISABLED` | notice | At startup, once per folder with `enabled = false`; the folder is not watched | — |
| `FOLDER_UNAVAILABLE` | warning | Watch path missing/unreadable this cycle | `folder`, `error` |
| `FILE_DETECTED` | info | New file path first observed | `size`, `mtime` |
| `FILE_CHANGED_DURING_SETTLE` | info | Stat or hash changed; settle window reset | `reason` (stat/hash/clock) |
| `FILE_VANISHED` | debug | Tracked file disappeared before processing | — |
| `FILE_READY` | info | Settle window passed | `size`, `sha256` (if verify_hash), `settled_seconds` |
| `FILE_SKIPPED` | info | Settled file not processed by policy (DESIGN §4.3) | `reason` (empty) |
| `FILE_LARGE` | warning | READY file is larger than 10 GiB; processing continues | `size` |
| `FILE_NEW_VERSION` | info | A DONE/FAILED file changed and will be reprocessed | `old_sha256` |
| `SYMLINK_REJECTED` | warning | Symlink found in watch dir, including a symlink marker (logged once per path) | — |
| `FILE_IGNORED_NONREGULAR` | debug | Fifo, socket, device, or (when not recursive) subdirectory in a watch folder; ignored (logged once per path) | `type` (fifo/socket/chardev/blockdev/dir/other) |
| `FILE_HARDLINK_DUPLICATE` | info | Two or more paths in one watch folder are hard links to the same file. Each path is still processed separately (logged once per extra path). | `inode`, `same_as` |
| `MARKER_SEEN` | info | Ready marker found for a tracked data file (DESIGN §4.4) | `marker` |
| `MARKER_ORPHAN` | warning | Ready marker with no matching data file (logged once per path) | `marker` |
| `MARKER_REMOVED` | info | Marker unlinked after successful processing | `marker` |
| `MARKER_REMOVE_FAIL` | warning | Marker could not be unlinked (file stays DONE) | `marker`, `error` |
| `PERMISSION_DENIED` | error | Cannot read source or write target | `op` (open_source/write_destination/read/scandir/rename/unlink_source), `error` |
| `ACTION_START` | debug | Action begins | `action`, `index` |
| `ACTION_OK` | info | Action succeeded and verified | `action`, `index`, `dest`, `sha256`, `bytes`, `duration_ms` |
| `ACTION_SKIPPED_DONE` | info | Action skipped on retry (already succeeded for this hash) | `action`, `index` |
| `ACTION_FAIL` | error | Action failed; file marked FAILED | `action`, `index`, `error` |
| `INTEGRITY_MISMATCH` | error | A SHA-256 comparison failed (stream vs settled, or dest vs source) | `stage`, `expected`, `actual` |
| `COLLISION_RESOLVED` | info | Destination name existed; policy applied | `policy` (suffix, or identical when the existing file already has the same SHA-256), `dest` |
| `DISPOSITION_OK` | info | Source left/moved/deleted | `mode`, `target` |
| `DISPOSITION_FAIL` | error | Disposition failed (actions already succeeded) | `mode`, `error` |
| `FILE_DONE` | info | File fully processed | `sha256`, `actions`, `manifest` (path or none) |
| `MANIFEST_WRITE_FAIL` | warning | Manifest could not be written (processing result unaffected) | `error` |
| `BATCH_SETTLING` | info | Folder batch (`trigger = "folder"`): new or changed files seen; the quiet window starts (DESIGN §4.6) | `files`, `bytes` |
| `BATCH_READY` | info | Folder unchanged for `settle_seconds`, files hashed (if `hash_batch`); the command is about to start | `batch`, `files`, `bytes`, `snapshot`, `settled_seconds` |
| `COMMAND_START` | info | Command started for a batch | `batch`, `program`, `sha256` (of the program), `pid`, `files`, `timeout_seconds` |
| `COMMAND_OUTPUT` | info | One line printed by the command (stdout and stderr together; escaped, at most 1,000 characters) | `batch`, `n` (line number), `line` |
| `COMMAND_OUTPUT_TRUNCATED` | warning | The command printed more than 10,000 lines; the rest are not logged | `batch`, `limit` |
| `COMMAND_BUSY` | info | The command exited with `busy_exit_code` (another job in progress); retried later | `batch`, `exit_code`, `duration_ms`, `output_lines`, `retry_in` |
| `COMMAND_FAIL` | error | The command exited non-zero, was killed by a signal, or could not be started; the batch is held | `batch`, `exit_code`, `duration_ms`, `output_lines`, `error`, `manifest` |
| `COMMAND_TIMEOUT` | warning | The command ran past `timeout_seconds` and was stopped (SIGTERM, then SIGKILL after 30 s); the batch is held | `batch`, `exit_code`, `duration_ms`, `output_lines`, `timeout_seconds`, `manifest` |
| `COMMAND_INTERRUPTED` | warning | The command was stopped by a service stop, or monfile restarted while it was running; the batch settles again | `batch`, `reason`, `pid`, `duration_ms`, `output_lines` |
| `COMMAND_REFUSED` | error | The program failed its ownership/mode checks (SR-13), or a batch folder was scanned by `scan --once`; nothing was run | `program`, `error` |
| `BATCH_DONE` | info | The command exited 0 and (with `expect_empty`) the folder is empty | `batch`, `exit_code`, `duration_ms`, `output_lines`, `files`, `remaining`, `manifest` |
| `BATCH_INCOMPLETE` | warning | The command exited 0 but files are left and `expect_empty = true`; the batch is held | `batch`, `exit_code`, `duration_ms`, `output_lines`, `remaining`, `manifest` |
| `RETRY_REQUESTED` | notice | Operator ran `monfile retry` (a file, or a held folder batch) | `user` (uid that ran the command), `sudo_user` (the `SUDO_USER` that invoked it via sudo, or none; informational) |
| `TEMP_CLEANUP` | info | Stale temp file removed at startup | `dest` |
| `DRY_RUN` | info | Prefixes the action/disposition events in `--dry-run` mode | `would` |
