# Operations Guide

> Draft: the commands below describe the intended behavior. The code is not implemented yet.

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
sudo ./packaging/install.sh            # add --fapolicyd on RHEL hosts running fapolicyd
```
`install.sh` creates the following. See `docs/audit/FILE_INVENTORY.md` for the full list.
- the `monfile` account
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
/opt/monfile/bin/monfile scan --once --dry-run # see what would happen
```
Manifests (if enabled) are in `/var/lib/monfile/manifests/<folder_id>/YYYY/MM/DD/`.

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
| Python files denied on RHEL | fapolicyd trust: `fapolicyd-cli --file add /opt/monfile/ && fapolicyd-cli --update` |
| `INTEGRITY_MISMATCH` | Source modified during copy, or storage fault. The file is FAILED and the source untouched. Investigate, then `retry`. |

## 7. Upgrade / uninstall
- **Upgrade:** stop the service, run the new `install.sh`, then re-run `fapolicyd-cli --update` and re-baseline AIDE. The config and state are preserved.
- **Uninstall:** `sudo ./packaging/uninstall.sh` removes the code and units. Add `--purge` to also remove the account. The config, state, and manifests are always preserved for audit, and must be removed manually if desired.
