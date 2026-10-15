# Operations Guide

> 0.4.0: implemented, unit-tested, and manually tested on Ubuntu 24.04. Folder batches and `--run-as` are so far only unit-tested. It has not yet been tested on RHEL 8/9 or Ubuntu 22.04.

## 1. Prerequisites
| OS | Install Python |
|---|---|
| RHEL 8 | `dnf install python3.11` |
| RHEL 9 | `dnf install python3.11` (or `python3.12`) |
| Ubuntu 22.04 | `apt install python3.11` (universe) |
| Ubuntu 24.04 | `python3` (3.12) is present by default |

## 2. Install
```sh
sha256sum -c SHA256SUMS
tar -xzf monfile-<version>.tar.gz
cd monfile-<version>
sudo ./packaging/install.sh --dry-run  # optional: review every change first
sudo ./packaging/install.sh --tarball-sha "$(cut -d' ' -f1 ../SHA256SUMS)"
```
`install.sh` checks every file against the tarball's `MANIFEST.sha256` before installing anything, and stops if a file was changed, added, or is a symlink. If fapolicyd is running, trust for `/opt/monfile/` is added automatically (`--no-fapolicyd` skips it, and `--fapolicyd` forces it). Each step is logged to syslog: `journalctl -t monfile-install`. Run `install.sh --help` for all options.

`install.sh` creates the following. See `docs/audit/FILE_INVENTORY.md` for the full list.
- the `monfile` account (unless `--run-as USER` names an existing non-root account to use instead; that choice is kept for upgrades, and `--run-as monfile` switches back. See SECURITY.md for the trade-off.)
- `/opt/monfile`
- `/etc/monfile/monfile.toml` (only if absent)
- the systemd units

## 3. Configure
1. Create the watch, destination, and processed directories. Grant the `monfile` group access to each:
   - **Watch dirs:** `r-x`, or `rwx` if `on_success` is `move` or `delete`.
   - **Destinations:** `rwx`. Make destination dirs setgid (`chmod g+s`) so delivered files (`0640`) take the directory's group.
   - **Folders with `ready_marker`:** the watch dir needs `rwx`, since monfile deletes consumed markers.
2. Edit `/etc/monfile/monfile.toml`. See `config/monfile.example.toml` for every option.
3. Allow the sandbox to write to those dirs:
   ```sh
   sudo systemctl edit monfile.service      # or monfile-scan.service
   # [Service]
   # ReadWritePaths=/data/inbound/a /data/outbound/a /data/processed/a
   ```
4. Validate: `sudo -u monfile /opt/monfile/bin/monfile validate-config`
5. **Folder batches** (`trigger = "folder"`, DESIGN §4.6): install each `command` program root-owned and not group/world-writable, in root-owned directories (e.g. `/usr/local/libexec/`, `0755`). If it needs network, `/home`, or other writable paths, add a reviewed `monfile.service.d/command.conf` based on `/opt/monfile/docs/examples/command-sandbox.conf`. Remove any cron job that ran the program before. Folder batches need the daemon.

## 4. Choose a run mode (enable ONE)
| Mode | Enable | When to prefer |
|---|---|---|
| Daemon | `systemctl enable --now monfile.service` | Steady inflow, lowest latency |
| Timer | `systemctl enable --now monfile-scan.timer` | Infrequent drops, nothing resident between runs |

If both are enabled, the second instance logs `LOCK_BUSY` and exits.

## 5. Day-to-day
```sh
journalctl -t monfile -f                       # live audit events
journalctl -t monfile | grep 'event=ACTION_FAIL'
/opt/monfile/bin/monfile status                # tracked files and states
/opt/monfile/bin/monfile retry <folder_id> <rel_path>
/opt/monfile/bin/monfile retry <folder_id>     # re-run a HELD folder batch
/opt/monfile/bin/monfile scan --once --dry-run # see what would happen
```
Manifests (if enabled) are in `/var/lib/monfile/manifests/<folder_id>/YYYY/MM/DD/`.

**Processed folders grow without limit.** monfile never removes archived originals (automatic expiry is roadmap item R1, DESIGN §14). Until then, a host can age them out with a systemd-tmpfiles rule, which runs as root under the host's control:
```sh
# /etc/tmpfiles.d/monfile-processed.conf: delete archived originals older than 30 days
e /data/processed/a - - - 30d
```
A deletion made this way is **not** recorded in monfile's audit log.

## 6. Troubleshooting
| Symptom | Check |
|---|---|
| File never processed | Is it excluded by a pattern? Is it still changing (`FILE_CHANGED_DURING_SETTLE`)? Is it a symlink? If the folder sets `ready_marker`, has the writer created the marker? Run `status`. |
| `MARKER_ORPHAN` | A marker exists with no data file. Check the writer's naming. monfile won't delete it. |
| Config change not taking effect | Config is read only at start. Run `systemctl restart monfile.service`. Reload and SIGHUP do nothing. |
| `FILE_SKIPPED reason=empty` | The folder has `skip_empty = true`, and the file was zero bytes when it settled. |
| `/var/lib/monfile` growing | Expected: state and manifests are kept indefinitely (AR-08). Archive per site policy. |
| `PERMISSION_DENIED` | Group membership, directory modes, `ReadWritePaths=` drop-in, SELinux AVCs (`ausearch -m avc -ts recent`) |
| `CONFIG_PERMS_INSECURE` | `chown root:monfile /etc/monfile/monfile.toml && chmod 0640 …` |
| Python files denied on RHEL | fapolicyd trust. `install.sh` adds it if fapolicyd was running at install time. Otherwise, re-run `install.sh`, or run `fapolicyd-cli --file add /opt/monfile/ && fapolicyd-cli --update` |
| `install.sh`: checksum mismatch or files not in MANIFEST | The extracted tree differs from the release. Delete it, re-verify the tarball with `sha256sum -c SHA256SUMS`, and extract again. |
| `install.sh`: units stopped and NOT restarted | The upgrade failed after stopping them. Fix the reported error and re-run `install.sh`, or start the listed units by hand. |
| Folder batch `HELD` | The last run failed, timed out, or left files (`COMMAND_FAIL`, `COMMAND_TIMEOUT`, `BATCH_INCOMPLETE`). Read its `COMMAND_OUTPUT` lines and the batch manifest, fix the cause, then `retry <folder_id>`. It also re-settles by itself when the folder changes. |
| `COMMAND_REFUSED` | The program or a directory above it isn't root-owned, is group/world-writable, or is a symlink. Or the folder was run by `scan --once` (batches need `monfile.service`). |
| `COMMAND_INTERRUPTED` | The service stopped (or crashed) during a run. The batch settles again and the program runs again; it must tolerate re-runs. |
| `INTEGRITY_MISMATCH` | Source modified during copy, or storage fault. The file is FAILED and the source untouched. Investigate, then `retry`. |

## 7. Upgrade / uninstall
- **Upgrade:** run the new release's `install.sh`. It stops whichever of `monfile.service` and `monfile-scan.timer` is running, swaps in the new code, refreshes fapolicyd trust, and starts the stopped units again. It doesn't change enablement, the config, or the state. Afterwards, compare your config with `/opt/monfile/docs/monfile.example.toml` for new options, and re-baseline AIDE.
- **Uninstall:** `sudo ./packaging/uninstall.sh` stops and disables the units, then removes them and their drop-ins, the fapolicyd trust, and `/opt/monfile`. The config, state, and manifests are always preserved for audit, and must be removed manually if desired. `--purge` also removes the `monfile` account, but only after `/etc/monfile` and `/var/lib/monfile` are gone. `--dry-run` shows what would be removed.
