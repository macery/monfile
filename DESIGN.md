# monfile — Design

| | |
|---|---|
| Status | **v0.3.0**: core implemented (scan, settle, copy/move, dispositions, markers, audit, manifests, CLI); manually tested on Ubuntu 24.04, not yet on RHEL 8/9 or Ubuntu 22.04 |
| Created | 2026-10-12 |
| Targets | RHEL 8/9, Ubuntu 22.04/24.04 (STIG-managed) |
| Runtime | Python ≥ 3.11, standard library only |

---

## 1. Purpose and scope

monfile watches configured folders for files dropped by people or other systems.
When a file has been **stable** for a configurable window (default 300 s), monfile runs that folder's **workflow**, which is an ordered chain of built-in actions such as copy or move.
It then disposes of the source file and records an auditable result.

### In scope (v1)
- Multiple watch folders, each with independent settings
- Stability detection for local-disk watch folders (the deployment target, §13 Q5). The algorithm does not depend on server clocks, so it also tolerates NFS/CIFS, but that is not tested in v1.
- Optional per-folder ready-marker files (`name.done`) for writers that pause mid-transfer (§4.4, ADR-0009)
- Built-in actions `copy` and `move`, with SHA-256 integrity verification
- Source disposition: `leave`, `move` (to a processed dir), or `delete`
- Audit trail through syslog/journald, plus optional per-folder JSON manifests
- Daemon mode and one-shot (timer/cron) mode
- Tarball + install script delivery

### Non-goals (v1)
- Running arbitrary external commands or scripts (see ADR-0006 and §6.4)
- Content inspection, AV scanning, or format conversion. These could become future actions.
- Remote destinations over network protocols (SFTP, S3, HTTP). Network mounts are only treated as local paths.
- Real-time (sub-second) reaction. The settle window dominates latency anyway.
- A GUI or web UI

## 2. Requirements summary
The full list with IDs is in [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md). In brief:
- **FR:** detect, settle, verify, execute, dispose, and support multiple folders.
- **SR:** least privilege, no shell, no symlink following, FIPS-safe hashing, strict config permissions.
- **AR:** every state transition and action outcome is logged, and manifests can be enabled.

## 3. Architecture

```
            ┌──────────────────────────── monfile process (user: monfile) ───────────────────────────┐
 config ──▶ │ config.py ──▶ cli.py (run | scan --once)                                               │
 (TOML)     │                    │  acquires flock /run/monfile/monfile.lock                          │
            │                    ▼                                                                   │
            │   ┌──── per folder, each cycle ────────────────────────────────────────────────┐       │
 watch dir ─┼──▶│ scanner.py ──▶ stability.py ──(READY)──▶ executor.py ──▶ actions/*         │       │
            │   │  lstat/scandir   state machine             runs chain      copy, move, …    │──────┼──▶ destination(s)
            │   │  filters         hashing.py                disposition                      │       │
            │   └────────┬───────────────┬──────────────────────┬────────────────────────────┘       │
            │            ▼               ▼                      ▼                                    │
            │        state.py (SQLite /var/lib/monfile/state.db)   audit.py ──▶ syslog (/dev/log)     │
            │                                                                ──▶ manifests/<folder>/  │
            └────────────────────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility |
|---|---|
| `cli.py` | Argument parsing, mode selection, process lock, signal handling (SIGTERM → finish the current file, then exit) |
| `config.py` | Load TOML, validate the schema and permissions, and produce immutable `FolderConfig` objects |
| `scanner.py` | Enumerate candidate files: `os.scandir`, `lstat` only, include/exclude globs, recursion, symlink rejection |
| `stability.py` | Per-file state machine (§4) |
| `hashing.py` | Chunked SHA-256 using `hashlib.sha256()` (I/O rate limiting: roadmap R2, §14) |
| `state.py` | SQLite persistence (§8) |
| `runner.py` | One scan cycle per folder (scan → evaluate → execute), vanish handling, once-per-path notices, startup recovery and temp cleanup, the daemon loop |
| `executor.py` | Run the action chain for a READY file, record per-action results, apply disposition, remove the marker, write the manifest |
| `actions/` | Action base class, registry, and built-in actions (§6). `actions/deliver.py` holds the shared atomic, verified delivery code. |
| `audit.py` | Emit structured events (§10) and write manifests |

## 4. Stability algorithm

A file must satisfy **both** conditions below before it is processed.
1. **Stat stability:** `(st_dev, st_ino, st_size, st_mtime_ns)` stays unchanged across scans for at least `settle_seconds`.
2. **Content stability** (when `verify_hash = true`): the SHA-256 taken at the **start** of the window equals the SHA-256 taken at the **end**.

### 4.1 Per-file state record
`folder_id, rel_path, dev, ino, size, mtime_ns, first_seen, stable_since, hash_start, hash_end, marker_seen_at, status, attempts, last_error, updated_at`

### 4.2 State machine
```
             new path seen
                  │
                  ▼
   ┌────────▶ SETTLING ──── stat changed ───┐ (reset stable_since=now, hash_start=NULL)
   │              │  ◀─────────────────────┘
   │              │ stat unchanged for 1 scan & hash_start is NULL → compute hash_start
   │              │ stat unchanged & now - stable_since ≥ settle_seconds
   │              │ & (ready_marker unset, or marker present)
   │              ▼
   │         HASH_CHECK ── compute hash_end ── hash_end ≠ hash_start ──▶ back to SETTLING
   │              │                                 (hash_start = hash_end, stable_since=now,
   │              │ equal (or verify_hash=false)     event FILE_CHANGED_DURING_SETTLE)
   │              ▼
   │           READY ──▶ IN_PROGRESS ──▶ DONE ──(disposition leave: record kept)
   │             └──(skip_empty & size 0)──▶ SKIPPED
   │                           │
   │                           └──▶ FAILED (log and skip, ADR-0005)
   │                                   │
   └──── stat changed (new version) ───┘   or `monfile retry <path>` → READY
```

- **Hash timing:** `hash_start` is computed on the first scan where the stat is unchanged. Hashing a file that is still being actively written wastes I/O. The settle window is measured from `stable_since`.
- **`hash_end` is reused** as the authoritative source hash for the integrity verification in §6.3. This avoids a third read of the source.
- **Stat is re-checked right after each hash.** If the stat changed while hashing, the result is discarded and the window resets.
- **Clock handling:** settle timing uses the monfile host's wall clock (`time.time()`), persisted so it survives restarts. File mtimes are only compared with their *previous* value, never with "now", so clock skew on NFS/CIFS servers does not matter. If `now < stable_since` (the clock went backwards), the window resets.

### 4.3 Edge cases
| Case | Behavior |
|---|---|
| File deleted mid-window | Drop the record and emit `FILE_VANISHED` at debug level |
| File renamed in the watch dir | Old path vanishes, and the new path starts a fresh window. Writers that use `name.part` → `name` work naturally with an `exclude` for `*.part`. |
| Truncated or replaced (new inode) | Detected by the stat tuple, so the window resets |
| Writer pauses longer than `settle_seconds` | **Cannot be distinguished from "done"** by stat alone. Mitigations: set `ready_marker` (§4.4), use a longer `settle_seconds`, or have the writer use temp names + rename. |
| Hardlinks | Processed per path. When two paths in the same watch folder share an inode, each extra path is logged once as `FILE_HARDLINK_DUPLICATE` (info), but not deduplicated (v1). Links to files outside the watch folder aren't detected. |
| Zero-byte file | Processed like any other file by default. With `skip_empty = true`, a settled zero-byte file is marked SKIPPED (`FILE_SKIPPED`, `reason=empty`) and not processed. If it later grows, it is a new version (§13 Q8). |
| Very large file | No size limit. A file over 10 GiB logs `FILE_LARGE` (warning) once, when it becomes READY, and is then processed normally (§13 Q3). |
| Non-regular file (fifo, socket, device, dir when not recursive) | Ignored and logged once as `FILE_IGNORED_NONREGULAR` (debug) |
| Symlink | Never followed. Logged once as `SYMLINK_REJECTED` (warning). |
| DONE file (disposition `leave`) changes later | Treated as a new version: `FILE_NEW_VERSION`, then it re-enters SETTLING and is processed again (§13 Q7, FR-18) |
| Unreadable file (EACCES) | `FAILED` with error, and `PERMISSION_DENIED` event |

### 4.4 Ready markers (optional, per folder)
Set `ready_marker = ".done"` on a folder for writers that can pause longer than any reasonable settle window (ADR-0009). Unset (the default) means settle-only behavior.
- **Gate:** data file `<name>` cannot become READY until a regular file `<name><suffix>` exists in the **same directory**. All other stability rules (§4.2) still apply, so the marker is necessary but not sufficient. The settle window and `hash_start` run as usual while the marker is absent. When the marker appears, `marker_seen_at` is recorded, `MARKER_SEEN` is emitted, and `hash_end` is checked at the next scan where the window is satisfied.
- **Marker files are never data:** any name ending in the suffix is excluded from processing, regardless of `include`. Marker contents are never read.
- **Markers are checked with `lstat`.** A symlink marker is ignored and logged as `SYMLINK_REJECTED`. It does not count.
- **On success:** after the disposition succeeds, monfile unlinks the marker (`MARKER_REMOVED`) for every disposition, including `leave`, so a later rewrite needs a fresh marker. If the unlink fails, `MARKER_REMOVE_FAIL` (warning) is emitted, and the file stays DONE.
- **On failure:** the marker is left in place, so `monfile retry` can proceed without the writer's help.
- **Orphan marker** (marker with no data file): ignored and logged once as `MARKER_ORPHAN` (warning). monfile never deletes a marker it did not consume.
- **Marker before data:** allowed. The data file still has to settle.
- **Marker removed before READY:** `marker_seen_at` is cleared, and the file waits again.

### 4.5 Cost
With `verify_hash = true`, each file is read **3×**: `hash_start`, `hash_end`, and the copy (the hash is computed while streaming and compared to `hash_end`). The destination is then read once more for verification. Folders with very large files can set `verify_hash = false`, which still keeps the destination integrity check.

## 5. Configuration schema

Path: `/etc/monfile/monfile.toml`, owned `root:monfile`, mode `0640`. monfile **refuses to start** if the config is group- or world-writable, or not owned by root (SR-06).
The fully commented reference is [config/monfile.example.toml](config/monfile.example.toml).

```toml
[global]
state_dir             = "/var/lib/monfile"            # state.db lives here
manifest_dir          = "/var/lib/monfile/manifests"
scan_interval_seconds = 30
log_level             = "info"                        # debug|info|warning|error
syslog_facility       = "daemon"
hash_chunk_bytes      = 1048576
max_files_per_cycle   = 1000                          # backpressure

[[folder]]
id              = "inbound-a"           # ^[a-z0-9][a-z0-9_-]{0,63}$, unique, used in logs and manifest path
enabled         = true                  # default; false = keep the block but don't watch the folder
path            = "/data/inbound/a"     # absolute, must exist, must not be a symlink
recursive       = false
settle_seconds  = 300
verify_hash     = true
skip_empty      = false                 # true = never process zero-byte files
manifest        = true                  # per-file JSON manifest on/off
include         = ["*"]                 # fnmatch on basename (exclude also prunes subdirectories when recursive)
exclude         = [".*", "*.part", "*.tmp", "~$*", "*.crdownload", "*.filepart"]   # default if omitted
on_success      = "move"                # leave | move | delete
ready_marker    = ".done"               # optional; unset = settle-only (§4.4)
processed_dir   = "/data/processed/a"   # required when on_success = "move"; must be outside `path`

  [[folder.action]]
  type        = "copy"
  destination = "/data/outbound/a"
  collision   = "suffix"                # suffix | skip | fail   (overwrite: not allowed in v1)
  preserve_tree = true                  # only meaningful when recursive = true
```

### Validation rules
- All paths are absolute, normalized, and contain no symlinks in any component (`realpath == path`).
- `processed_dir` and every action `destination` are outside the watch `path`, so monfile can't re-ingest its own output.
- No two folders' `path` values overlap.
- A `move` action, if present, must be **last** in the chain, and `on_success` must then be `leave`, since the source is already gone.
- Unknown keys are an **error**, not a warning. This catches typos in audited configs.
- `settle_seconds ≥ 2 × scan_interval_seconds`. Otherwise the window can be satisfied in a single observation.
- `ready_marker`, if set, matches `^\.[A-Za-z0-9_-]{1,32}$` (a dot followed by a short name, such as `.done` or `.ready`). Markers are recognized **before** include/exclude filtering, so no filter can hide them.
- `[global]`, `[[folder]]` and `[[folder.action]]` are the only section names. A folder is named by its `id`.
- `enabled = false` (default `true`) turns a folder off. A disabled folder's block is still fully schema-checked, and its `id` still counts for uniqueness, but its paths are not checked for existence, symlinks, overlap, or access, and it is never scanned. Its state records are kept, so re-enabling it resumes where it stopped. At startup monfile logs `FOLDER_DISABLED` for each one. At least one `[[folder]]` block is required, but all of them may be disabled; the service then runs and watches nothing. The shipped example has every folder disabled. Like any config change, toggling `enabled` takes effect on restart (§13 Q10).
- Every folder has at least one action. `processed_dir` is allowed only with `on_success = "move"`.
- No output directory (action destination or `processed_dir`) may be inside, or contain, **any** folder's watch path.
- `validate-config` also checks that the invoking user can read every watch folder and write every output directory (and the watch folder when files are removed from it). Run it as `monfile`. `--schema-only` skips ownership, path, and access checks, for reviewing a config off-host.

## 6. Actions

### 6.1 Interface
```python
class Action(ABC):
    type_name: ClassVar[str]
    def __init__(self, params: Mapping[str, Any]) -> None: ...   # validate params at config load
    def run(self, ctx: ActionContext) -> ActionResult: ...         # must be idempotent
```
`ActionContext` carries the folder config, the source path, the source SHA-256 (`hash_end`), `dry_run`, the audit handle, and the results of earlier actions in the chain.
`ActionResult` carries `ok`, `destination`, `dest_sha256`, `bytes`, `started`, `finished`, and `error`.

### 6.2 Chain semantics
- Files are processed **one at a time** within a cycle, in scan order (§13 Q15). This bounds I/O load and keeps the audit log in a simple order.
- Actions run sequentially. On the first failure the chain stops, the file goes to `FAILED`, and no disposition is applied.
- Each action's result is persisted (§8). A retry **skips actions already recorded as successful** for the same source hash, which makes partial chains safe to resume.

### 6.3 Built-in actions
| Action | Behavior |
|---|---|
| `copy` | Open the source with `O_NOFOLLOW`. Stream to `<dest>/.monfile-tmp-<random>` (mode 0600 during write), computing SHA-256 while streaming. Compare the stream hash to `hash_end`, `fsync` the file, re-read the temp file and verify its SHA-256, set the final mode `0640` (§7), then **hard-link** the temp file to the final name and unlink the temp file. `link()` fails if the name exists, so a file that appears concurrently is never overwritten. On filesystems without hard links, it falls back to check-then-rename. Finally `fsync` the directory. Subdirectories (with `preserve_tree`) are created and opened one component at a time with `O_NOFOLLOW`. The source is re-stat'ed before and after the copy, and the action fails if it no longer matches the settled stat. |
| `move` | Default: same as `copy`, then `unlink` the source. This keeps SELinux labels correct, since the file gets the destination's context, and always verifies. An optional `mode = "rename"` uses `os.rename` on the same filesystem, which is faster but keeps the source's SELinux label (§12). Rename mode needs the watch folder and destination in the **same** mount. Separate `ReadWritePaths=` entries are separate mounts, so list a common parent directory instead. Otherwise the action fails with an explanatory error and never falls back silently. |

### 6.4 Future: external command action (not in v1)
If it is ever added, it must meet all of these:
- an absolute-path allow-list in root-owned config
- `subprocess.run([...], shell=False)` with a fixed argv template
- a scrubbed environment
- a timeout
- the same service user
- stdout/stderr size-capped and logged
- a new ADR and REQUIREMENTS entries

## 7. Source disposition and collisions
- **`leave`:** the source stays put. The DONE record prevents reprocessing until the stat changes.
- **`move`:** the source is moved to `processed_dir`, preserving the relative tree if the folder is recursive. The collision policy is always `suffix`, since a failure after delivery would be worse than a renamed archive copy. A rename is tried when the source and `processed_dir` have the same `st_dev`. If it fails with `EXDEV`, monfile falls back to copy + verify + unlink. Under systemd, each `ReadWritePaths=` entry is a separate bind mount, and `rename()` can't cross mount points even on one filesystem.
- **`delete`:** the source is unlinked only after **every** action succeeded and verified.
- **Identical content is not a collision:** under every policy, if the existing file's SHA-256 equals the source's, the delivery counts as already done (`COLLISION_RESOLVED policy=identical`). This makes retries idempotent, for example after a crash between delivery and recording the result.
- **Collision policies** (when the destination name exists with different content):
  - `suffix`: `name.YYYYmmddTHHMMSSZ.ext`, plus `-N` if still taken
  - `skip`: same as `fail` when the content differs. It exists for configs that want identical re-deliveries accepted explicitly.
  - `fail`: mark the file FAILED
- **Delivered file ownership and mode** (§13 Q4): owner `monfile`, mode `0640`, fixed in v1. The group is the destination directory's group when that directory is setgid (recommended; see OPERATIONS §3). Otherwise it is `monfile`'s primary group. monfile never calls `chown`, since that would need privileges it doesn't have.

## 8. State store and crash recovery
SQLite at `<state_dir>/state.db` (WAL mode, `0600 monfile:monfile`).

```sql
files(folder_id, rel_path BLOB, dev, ino, size, mtime_ns, first_seen, stable_since,
      hash_start, hash_end, marker_seen_at, status, gone, attempts, last_error, updated_at,
      PRIMARY KEY(folder_id, rel_path))
files_history(<same columns>, archived_at)   -- superseded records (new version, name reused)
action_results(folder_id, rel_path, source_key, action_index, action_type,
      ok, destination, dest_sha256, bytes, started, finished, error)
notices(folder_id, rel_path, kind)           -- "log once" bookkeeping (symlinks, orphan markers)
meta(key PRIMARY KEY, value)   -- schema_version, created_at
```

- `rel_path` is stored as raw bytes, so non-UTF-8 filenames round-trip.
- `source_key` is the settled SHA-256, or `stat:<dev>:<ino>:<size>:<mtime_ns>` when `verify_hash = false`.
- `gone = 1` marks a DONE/SKIPPED/FAILED record whose source no longer exists (consumed by a move/delete, or removed by someone else). If a file with the same name appears later, it is a **new file** (`FILE_DETECTED`). If a DONE file that is still present changes, it is a **new version** (`FILE_NEW_VERSION`). Either way the old record moves to `files_history`.
- A file that vanishes before it is processed has its record dropped (`FILE_VANISHED`), since there is nothing to audit.

- **Startup recovery:**
  - Records in `IN_PROGRESS` go back to `READY` and are re-executed. Actions are idempotent and completed steps are skipped (§6.2).
  - Stale `.monfile-tmp-*` files in configured destinations older than 1 h are removed, with a `TEMP_CLEANUP` event.
- **Retention** (§13 Q6, AR-08): monfile never prunes state records or deletes manifests. Both are kept indefinitely as audit records. The host owner sizes `/var/lib/monfile` accordingly and archives it by site procedure.
- **Locking:** `fcntl.flock` on `/run/monfile/monfile.lock`. The daemon holds it for its lifetime. A timer run that can't get the lock exits 0 with a `LOCK_BUSY` info event. This also prevents running both modes at once.

## 9. Security model
| Control | Implementation |
|---|---|
| Least privilege | A dedicated system user and group `monfile` (nologin shell, no home). Group ACLs or memberships on watch/dest dirs are granted by the host admin. |
| systemd hardening | `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`, an empty `CapabilityBoundingSet`, `RestrictAddressFamilies=AF_UNIX`, `ReadWritePaths` limited to configured dirs (via a drop-in), `UMask=0027`, and more (see the unit file) |
| No shell | No subprocess usage at all in v1 |
| No symlinks | `lstat` everywhere, `O_NOFOLLOW` on open, config paths validated with `realpath` |
| Path traversal | Destination names are built from `rel_path` components. The resolved destination must remain under the destination root. Names containing `/`, NUL, or `..` components are rejected. |
| Config integrity | Root-owned, not writable by group or others, otherwise refuse to start. The config is reloaded only on restart (§13 Q10). SIGHUP is ignored, so an accidental HUP can't interrupt a file mid-action. The config hash is logged (`CONFIG_LOADED`). |
| Crypto | SHA-256 only, which is FIPS 180-4 and works in FIPS mode. `hashlib.sha256(usedforsecurity=True)` is the default. |
| Code integrity | Installed root-owned under `/opt/monfile`, bytecode precompiled at install, tarball delivered with `SHA256SUMS`. `install.sh` verifies every file against the tarball's `MANIFEST.sha256` in a private staging dir before installing, and adds fapolicyd trust automatically when fapolicyd is running (ADR-0010). |
| Logging hygiene | No file contents are logged. Paths are logged with control characters escaped, which prevents log injection. |

## 10. Audit and logging
### 10.1 Syslog/journald (always on)
- Emitted with stdlib `logging.handlers.SysLogHandler(address="/dev/log")`. This works on both rsyslog and journald hosts, so existing forwarding to the SIEM applies.
- Ident: `monfile`, facility: configurable (default `daemon`).
- Format: `event=<ID> folder=<id> path="<escaped>" key=value ...`
- The event catalog is in [docs/audit/AUDIT_EVENTS.md](docs/audit/AUDIT_EVENTS.md).

### 10.2 Per-file manifests (per-folder toggle `manifest = true|false`)
- Path: `<manifest_dir>/<folder_id>/<YYYY>/<MM>/<DD>/<utc-ts>_<sha256[:12]>_<basename>.json`. The manifest is written atomically.

```json
{
  "schema_version": 1,
  "monfile_version": "0.1.0",
  "host": "hostname",
  "folder_id": "inbound-a",
  "source": {"path": "/data/inbound/a/report.pdf", "size": 12345, "mtime": "…Z",
             "sha256": "…", "first_seen": "…Z", "ready_at": "…Z"},
  "actions": [{"index": 0, "type": "copy", "destination": "/data/outbound/a/report.pdf",
               "ok": true, "dest_sha256": "…", "verified": true,
               "started": "…Z", "finished": "…Z", "error": null}],
  "disposition": {"mode": "move", "target": "/data/processed/a/report.pdf", "ok": true},
  "marker": {"path": "/data/inbound/a/report.pdf.done", "seen_at": "…Z", "removed": true},
  "status": "DONE"
}
```

## 11. Run modes
| Mode | Command | Unit | Notes |
|---|---|---|---|
| Daemon | `monfile run` | `monfile.service` (Type=simple, Restart=on-failure) | Loops every `scan_interval_seconds`. SIGTERM triggers a graceful stop. |
| One-shot | `monfile scan --once` | `monfile-scan.service` + `monfile-scan.timer` (e.g. every 1 min) | Same code path, one cycle per invocation. State in SQLite makes the windows span runs. |

Enable **only one** mode per host. The lock enforces this at runtime, and OPERATIONS.md documents it.

Other CLI commands:
- `validate-config [--schema-only]`: validate the config, its permissions, and the invoking user's access to every folder, then exit
- `status [--all]`: summary counts, plus a table of files that are not DONE/SKIPPED (all files with `--all`), with the last error for FAILED ones
- `retry <folder_id> <rel_path>`: send a FAILED file back to READY
- `--dry-run`: available on `run`/`scan`; logs the actions it would take as `DRY_RUN would=<event>` without performing them. Settle tracking is still recorded, but no file is moved, written, or marked DONE.
- `-c FILE` may be given before or after the subcommand.
- **Exit codes:** 0 success (including `LOCK_BUSY` in scan mode); 1 runtime error, `LOCK_BUSY` in run mode, or `validate-config` access problems; 2 invalid or insecure config. `monfile.service` sets `RestartPreventExitStatus=2`, so a bad config stops the service instead of restart-looping.

## 12. Platform notes
| Topic | RHEL 8 | RHEL 9 | Ubuntu 22.04 | Ubuntu 24.04 |
|---|---|---|---|---|
| Python ≥ 3.11 | `python3.11` (AppStream) | `python3.11` / `python3.12` (AppStream) | `python3.11` (universe) | `python3` (3.12, default) |
| MAC | SELinux enforcing | SELinux enforcing | AppArmor | AppArmor |
| App allow-listing | fapolicyd (if enabled) | fapolicyd (if enabled) | n/a by default | n/a by default |
| FIPS | possible | possible | possible (Ubuntu Pro FIPS) | possible (Ubuntu Pro FIPS) |

- **SELinux:** files created in a destination inherit that directory's default context. A rename keeps the source context, which is why `move` defaults to copy + verify + unlink. The service runs as `unconfined_service_t`. No custom policy module ships in v1 (§13 Q11).
- **Ubuntu 22.04:** `python3.11` comes from the *universe* component, which is approved for the target hosts (§13 Q12).
- The interpreter path is resolved at install time and written into the launcher `/opt/monfile/bin/monfile`.
- **Install/upgrade** (ADR-0008, ADR-0010): `install.sh` builds `/opt/monfile.new`, stops any running unit, swaps the tree in, compiles bytecode, and restarts what was running. Each step is logged to syslog with tag `monfile-install`, and `--dry-run` shows every change without making it.

## 13. Open questions
| # | Question | Proposed default | Status |
|---|---|---|---|
| Q1 | Default `scan_interval_seconds`? | 30 s | **resolved 2026-10-12:** default accepted |
| Q2 | Default exclude patterns for temp/partial files? | `.*`, `*.part`, `*.tmp`, `~$*`, `*.crdownload`, `*.filepart` | **resolved 2026-10-12:** default accepted (used when `exclude` is omitted) |
| Q3 | Max file size limit? | none (warn > 10 GiB) | **resolved 2026-10-12:** default accepted: `FILE_LARGE` warning (§4.3) |
| Q4 | Ownership/mode of delivered files? | `monfile:<dest group>`, `0640` | **resolved 2026-10-12:** `0640`, fixed for now (§7) |
| Q5 | Are watch dirs on NFS/CIFS? Which ones? | design assumes possibly | **resolved 2026-10-12:** local disk only. NFS/CIFS is untested in v1 (§1). |
| Q6 | Retention for state records and manifests? | 90 days state, manifests managed by host (tmpfiles/cron) | **resolved 2026-10-12:** kept indefinitely; monfile never prunes (§8, AR-08) |
| Q7 | Re-process a `leave`-disposition file when it changes later? | yes, as a new version, event `FILE_NEW_VERSION` | **resolved 2026-10-12:** yes (§4.3, FR-18) |
| Q8 | Process zero-byte files? | yes, configurable `skip_empty` | **resolved 2026-10-12:** default accepted: `skip_empty`, default `false` (§4.3) |
| Q9 | Sentinel/marker-file support (e.g. `name.done`) for writers that pause? | not in v1 | **resolved 2026-10-12:** yes, in v1, per-folder `ready_marker` (§4.4, ADR-0009, FR-17) |
| Q10 | SIGHUP config reload, or restart only? | restart only (simpler to audit) | **resolved 2026-10-12:** default accepted: SIGHUP ignored (§9) |
| Q11 | Custom SELinux policy module (confined domain) for RHEL? | not v1; run unconfined_service_t | **resolved 2026-10-12:** default accepted |
| Q12 | Is Ubuntu universe (`python3.11` on 22.04) allowed? | confirm with host owners | **resolved 2026-10-12:** yes |
| Q13 | Alerting on FAILED beyond syslog (e.g. email)? | syslog/SIEM only | **resolved 2026-10-12:** syslog/SIEM only. Alert rules live in the SIEM. |
| Q14 | `move` default mode: copy+verify+unlink vs rename? | copy+verify+unlink | **resolved 2026-10-12:** default accepted (§6.3) |
| Q15 | Concurrency: process files in parallel within a cycle? | sequential v1 | **resolved 2026-10-12:** default accepted (§6.2) |

## 14. Roadmap (candidate features, not scheduled)
These items are recorded so the idea and its constraints aren't lost. None are committed to a release. Adopting one requires an ADR, new REQUIREMENTS IDs, catalog events (if any), and a CHANGELOG entry, per AGENTS.md.

| # | Item | Notes / constraints | Status |
|---|---|---|---|
| R1 | **Retention for `processed_dir` files.** An optional per-folder `processed_retention_days`. monfile would remove archived originals older than that, logging one event per file (e.g. `PROCESSED_EXPIRED` with path, size, SHA-256). | Today processed files are kept until someone removes them, and monfile never deletes them. Applies only to `processed_dir` contents, never to state or manifests (AR-08 stays). Age should come from the archive time recorded in state, not the file mtime (which is the writer's). Only files monfile itself archived should be removed, matched by state record and SHA-256. Interim option without code: a host-managed systemd-tmpfiles age rule on the processed dir (OPERATIONS §5). | proposed 2026-10-15 |
| R2 | **Hash/copy I/O rate limiting.** An optional global `hash_max_bytes_per_second` (0 = unlimited) to cap read throughput on busy shared disks. | Originally listed in §3 as optional, but never in the config schema. Must not weaken verification: limit throughput only, never skip reads. | proposed 2026-10-15 |
