# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Folder batches** (`trigger = "folder"`, FR-22, ADR-0011). A folder can be settled as a whole: once nothing in it has been added, changed or removed for `settle_seconds`, one action runs for the whole batch. While it runs, other folders keep being scanned. Folder batches need the daemon.
- **`command` action** (FR-23, SR-13, AR-09), for folder batches only. Runs one program named in the config, without a shell, with fixed `args`, a fixed environment, and a timeout (`timeout_seconds`, default 2 h; SIGTERM to its process group, then SIGKILL). Before every run monfile checks that the program and every directory above it are root-owned and not group/world-writable, with no symlinks, and logs its SHA-256. Each output line is logged (`COMMAND_OUTPUT`). `busy_exit_code` marks "already running, retry in 5 minutes". `expect_empty` (default on) makes success require an empty folder. A failed batch is held until the folder changes or `monfile retry <folder>`. Each run gets a manifest listing the batch's files with SHA-256 (`hash_batch`, default on). The program receives `MONFILE_FOLDER_ID`, `MONFILE_FOLDER_PATH`, `MONFILE_BATCH_ID`, and `MONFILE_BATCH_LIST` (a NUL-separated list of the batch's files).
- New events: `BATCH_SETTLING`, `BATCH_READY`, `COMMAND_START`, `COMMAND_OUTPUT`, `COMMAND_OUTPUT_TRUNCATED`, `COMMAND_BUSY`, `COMMAND_FAIL` (error), `COMMAND_TIMEOUT`, `COMMAND_INTERRUPTED`, `COMMAND_REFUSED` (error), `BATCH_DONE`, `BATCH_INCOMPLETE`.
- `install.sh --run-as USER`: run the service as an existing non-root account (and its primary group) instead of creating `monfile`. It writes a `User=`/`Group=` drop-in for both service units, sets `/run/monfile`'s owner, re-owns `/var/lib/monfile` and changes the config's group when the account changes, and is remembered on upgrades (`--run-as monfile` switches back). It refuses root and never creates or changes the account.
- `monfile retry <folder_id>` (no path) re-runs a held folder batch. `status` lists batch folders with their state and last run. `validate-config` shows each batch folder's program and timeout.
- `docs/examples/command-sandbox.conf`: an example drop-in for loosening the sandbox for a batch program that needs network, `/home`, or more writable paths.

### Changed
- ADR-0011 supersedes ADR-0006 ("no external commands"). AGENTS.md rule 3 now allows `subprocess` only in the `command` action, with `shell=False`. SR-01 and SR-03 are reworded to match.
- The state database schema is now version 2 (three new batch tables). A version-1 database is upgraded in place on first start. **An older monfile will refuse the upgraded database**, so downgrading needs a backup of `/var/lib/monfile/state.db` taken before upgrading.
- The example config has a third (disabled) folder showing a folder batch.

## [0.3.0] - 2026-10-15
Adds a per-folder `enabled` switch and ships the example folders disabled. The core is unchanged from 0.2.0; the new switch is covered by unit tests. Manually tested on Ubuntu 24.04, but not yet on RHEL 8/9 or Ubuntu 22.04.

### Added
- Per-folder `enabled = true|false` (default `true`, FR-21). A disabled folder stays in the config and is still schema-checked, but it isn't scanned and its directories needn't exist. The service logs the new `FOLDER_DISABLED` event (notice) for each one at startup, `CONFIG_LOADED` gains a `disabled` count, `validate-config` lists disabled folders, and `retry` refuses them with an explanation.

### Changed
- Both folders in `config/monfile.example.toml` now ship with `enabled = false`, so a config copied from the example loads cleanly and watches nothing until edited. A fresh install's `/etc/monfile/monfile.toml` is that example, so the service now starts idle instead of failing on the example's missing `/data` paths.

### Documentation
- The example config explains that `[global]`, `[[folder]]` and `[[folder.action]]` are fixed section names, and that a folder is named by its `id`.

## [0.2.0] - 2026-10-15
First working release: monfile now processes files. It has been manually tested on Ubuntu 24.04, but not yet on RHEL 8/9 or Ubuntu 22.04.

### Added
- `install.sh` is implemented (ADR-0010). It verifies every file against the tarball's new `MANIFEST.sha256` in a private staging dir; creates the `monfile` account; installs `/opt/monfile` with precompiled bytecode, the config (only if absent), the state dir, and the systemd units; and smoke-tests the launcher as `monfile`. Options: `--python`, `--tarball-sha`, `--fapolicyd`/`--no-fapolicyd`, `--allow-unsupported-os`, `--dry-run`.
- Upgrades in place: the running units are stopped, the new tree is swapped in, and the units that were running are started again. Config, state, and enablement are left unchanged.
- fapolicyd trust is added automatically when fapolicyd is running, and refreshed on upgrade.
- `uninstall.sh` is implemented, with `--purge` (refused while `/etc/monfile` or `/var/lib/monfile` exist) and `--dry-run`.
- Install and uninstall steps are logged to syslog with tag `monfile-install`.
- `build-tarball.sh` writes `MANIFEST.sha256` into the tarball and refuses symlinks. `DIST=` overrides the output dir.
- `/opt/monfile/docs/monfile.example.toml`: the reference config ships with each install, for comparing after upgrades.
- **The program is implemented.** Config loading and validation; polling scans that never follow symlinks; the settle state machine (stat + SHA-256 at the start and end of the window); `copy` and `move` actions with atomic, verified, no-clobber delivery; `leave`/`move`/`delete` dispositions; ready markers; `skip_empty`; audit events to syslog (or stderr, with journald priorities, if `/dev/log` is missing); per-file manifests; SQLite state with crash recovery and stale temp cleanup; and the `run`, `scan --once`, `validate-config`, `status`, and `retry` commands.
- `validate-config --schema-only` checks a config without ownership, path, or access checks. Without it, `validate-config` also reports folders that the invoking user can't read or write.
- `status --all` includes DONE and SKIPPED files.
- New audit events: `FILE_IGNORED_NONREGULAR` (debug; fifos, sockets, devices, and subdirectories of non-recursive folders, logged once per path) and `FILE_HARDLINK_DUPLICATE` (info; paths in one watch folder that share an inode, logged once). Both close gaps between DESIGN §4.3 and the code.
- A CI workflow runs the tests on every push and pull request, on Ubuntu 24.04 (Python 3.12) and 22.04 (Python 3.11, the minimum).
- `-c FILE` is accepted before or after the subcommand.
- Exit code 2 means an invalid or insecure config. `monfile.service` sets `RestartPreventExitStatus=2`, so a bad config no longer causes a restart loop.

### Fixed
- The processed-folder disposition failed with "Invalid cross-device link" under systemd when the watch and processed folders were separate `ReadWritePaths=` entries, even on one filesystem. It now falls back to copy + verify + unlink on `EXDEV`. A `move` action with `mode = "rename"` reports the cause instead of the bare error.

### Changed
- I/O rate limiting for hashing, listed as optional in DESIGN §3 but never specified, moved to the roadmap (DESIGN §14, R2).
- Under every collision policy, an existing destination file with identical content counts as already delivered (`COLLISION_RESOLVED policy=identical`). This makes retries idempotent.
- The processed-folder disposition always uses the `suffix` collision policy.
- Ready markers are recognized before include/exclude filtering. The rule that `exclude` must not match the marker was dropped.
- `exclude` patterns also prune subdirectories when `recursive = true`.

## [0.1.0] - 2026-10-12
First release. **Design and documentation only:** the modules are stubs, and the tool does not process files yet.

### Added
- `HOWTO.md`: a step-by-step guide to setting up and using monfile, with a worked example and common recipes. It ships in the tarball, is installed to `/opt/monfile/docs/`, and is attached to each GitHub release.
- MIT license (`LICENSE`).
- GitHub Actions release workflow: on a `v*` tag, it runs the tests, builds the tarball and `SHA256SUMS`, and publishes them as a GitHub release.
- Initial project scaffolding: design docs, requirements, ADRs 0001–0009, audit documentation, the third-party inventory, the example config, stub package, systemd units, and install/build script drafts.
- Optional per-folder `ready_marker` setting (e.g. `.done`): a file is processed only after its marker exists, and the marker is removed on success (ADR-0009). New audit events: `MARKER_SEEN`, `MARKER_ORPHAN`, `MARKER_REMOVED`, `MARKER_REMOVE_FAIL`.
- Optional per-folder `skip_empty` setting (default `false`), with the `FILE_SKIPPED` event.
- `FILE_LARGE` warning event for files over 10 GiB (no size limit).

### Decided
- Watch folders are local disk. Delivered files are mode `0640`. Changed DONE files are reprocessed as new versions. State and manifests are kept indefinitely. Ubuntu universe is allowed for `python3.11` on 22.04. Failure alerting is syslog/SIEM only (DESIGN §13 Q4–Q7, Q9, Q12, Q13).
- The proposed defaults were accepted for the remaining open questions: 30 s scan interval, the default exclude list, restart-only config reload (SIGHUP ignored), no custom SELinux policy in v1, `move` as copy + verify + unlink, and sequential processing (DESIGN §13 Q1–Q3, Q8, Q10, Q11, Q14, Q15). All design questions are now closed.
