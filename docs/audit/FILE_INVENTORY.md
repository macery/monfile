# Installed File Inventory

This lists every path that `packaging/install.sh` creates or modifies. Use it for:
- audit
- AIDE/file-integrity baselines
- fapolicyd trust
- clean uninstall

Keep it in sync with `packaging/` (AGENTS.md).

| Path | Type | Owner:Group | Mode | Purpose | Created by | Removed by uninstall |
|---|---|---|---|---|---|---|
| `/opt/monfile/` | dir | root:root | 0755 | Install root | install.sh | yes |
| `/opt/monfile/bin/monfile` | file | root:root | 0755 | Launcher: Python script with `#!<python> -I` shebang that prepends `/opt/monfile/lib` to `sys.path` (isolated mode ignores env vars and user site-packages) | install.sh | yes |
| `/opt/monfile/lib/monfile/**/*.py` | files | root:root | 0644 | Application code | install.sh | yes |
| `/opt/monfile/lib/monfile/**/__pycache__/` | dir/files | root:root | 0755/0644 | Precompiled bytecode (`compileall`) | install.sh | yes |
| `/opt/monfile/docs/**` | dirs / files | root:root | 0755 / 0644 | Documentation, including `HOWTO.md` and `OPERATIONS.md` (referenced by the units' `Documentation=`) | install.sh | yes |
| `/opt/monfile/VERSION` | file | root:root | 0644 | `key=value`: version, tarball SHA-256 (`--tarball-sha`), install time (UTC), installing user, Python path and version | install.sh | yes |
| `/opt/monfile/docs/monfile.example.toml` | file | root:root | 0644 | Reference config for this version (part of `docs/**`) | install.sh | yes |
| `/opt/monfile.new/`, `/opt/monfile.old/` | dir | root:root | 0755 | **Transient**: the next and previous trees during the upgrade swap (ADR-0010). Present only if a run was interrupted; the next install.sh cleans them up. | install.sh | yes |
| `/etc/monfile/` | dir | root:monfile | 0750 | Config dir | install.sh | **no** (preserved) |
| `/etc/monfile/monfile.toml` | file | root:monfile | 0640 | Active config (example copied only if absent; only its group is changed when the service account changes) | install.sh / admin | **no** |
| `/etc/systemd/system/monfile.service` | file | root:root | 0644 | Daemon unit | install.sh | yes |
| `/etc/systemd/system/monfile-scan.service` | file | root:root | 0644 | One-shot unit | install.sh | yes |
| `/etc/systemd/system/monfile-scan.timer` | file | root:root | 0644 | Timer for one-shot mode | install.sh | yes |
| `/etc/systemd/system/monfile.service.d/paths.conf` | file | root:root | 0644 | `ReadWritePaths=` / `ReadOnlyPaths=` for configured dirs (admin-maintained) | admin | yes |
| `/etc/systemd/system/monfile-scan.service.d/paths.conf` | file | root:root | 0644 | Same, for scan mode | admin | yes |
| `/etc/systemd/system/monfile.service.d/10-run-as.conf`, `/etc/systemd/system/monfile-scan.service.d/10-run-as.conf` | file | root:root | 0644 | `User=`/`Group=` of an existing account, only with `install.sh --run-as` (ADR-0011). Removed by `--run-as monfile`. | install.sh | yes |
| `/etc/systemd/system/monfile.service.d/command.conf` | file | root:root | 0644 | Optional sandbox loosening for a folder batch's `command` program (from `docs/examples/command-sandbox.conf`) | admin | yes |
| `/etc/tmpfiles.d/monfile.conf` | file | root:root | 0644 | Creates `/run/monfile`, owned by the service account | install.sh | yes |
| `/run/monfile/` | dir | monfile:monfile | 0750 | Lock file location (`RuntimeDirectory=`) | systemd | n/a (tmpfs) |
| `/var/lib/monfile/` | dir | monfile:monfile | 0750 | State dir (`StateDirectory=`) | install.sh / systemd | **no** (audit data) |
| `/var/lib/monfile/state.db` (+ `-wal`, `-shm`) | file | monfile:monfile | 0600 | State DB | monfile | **no** |
| `/var/lib/monfile/manifests/` | dir | monfile:monfile | 0750 | Per-file and per-batch manifests | monfile | **no** |
| `/var/lib/monfile/batches/` | dir | monfile:monfile | 0750 | **Transient** `<batch_id>.list` files (0640, NUL-separated relative paths) handed to a running `command` program as `MONFILE_BATCH_LIST`; each is removed when its run ends | monfile | **no** |
| user/group `monfile` | account | — | — | System account, `nologin` shell, no home. Not created with `--run-as USER`; that existing account is never created, changed, or removed by monfile. | install.sh | optional (`--purge`, only after `/etc/monfile` and `/var/lib/monfile` are removed) |
| `/etc/fapolicyd/fapolicyd.trust` (entries under `/opt/monfile/`) | trust entries | root:fapolicyd | (fapolicyd's) | fapolicyd trust for the code and bytecode, refreshed on every install. Only when fapolicyd is running, or with `--fapolicyd`. | install.sh (via `fapolicyd-cli`) | yes |
| `$TMPDIR/monfile-install.XXXXXXXX/` | dir | root:root | 0700 | **Transient**: verified staging copy of the release, removed when install.sh exits | install.sh | n/a |

Single files (the launcher, `VERSION`, config, units, tmpfiles) are written as a temp file named `.<name>.XXXXXXXX` in the same directory, then renamed into place. A temp file is left behind only if install.sh is killed mid-write.

**Service account:** the `monfile:monfile` owners above become the `--run-as` user and its primary group when that option is used. On a change of account, install.sh re-owns `/var/lib/monfile` recursively and changes the config's group.

**Not created by monfile:** watch dirs, destination dirs, processed dirs, and `command` programs. The host admin creates these and grants the service account access. A `command` program must be root-owned and not group/world-writable, in root-owned directories (SR-13).
