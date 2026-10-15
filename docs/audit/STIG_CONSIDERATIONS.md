# STIG / NIST 800-53 Considerations

This document maps monfile's design to the control families that assessors typically examine.
It is **guidance for the host owner and assessor**, not a certification. Validate STIG rule IDs against the current RHEL 8/9 and Canonical Ubuntu 22.04/24.04 STIG releases used by your organization.

## Control mapping

| NIST 800-53 | Topic | How monfile addresses it | Evidence |
|---|---|---|---|
| AC-6 | Least privilege | Dedicated `monfile` system account (nologin), no capabilities, `NoNewPrivileges=yes`, no root at runtime. Optionally an existing non-root account (`--run-as`, ADR-0011). | `packaging/systemd/monfile.service`, FILE_INVENTORY |
| AU-2 / AU-3 | Event logging / content | Defined event catalog with what/where/outcome/identity fields | `docs/audit/AUDIT_EVENTS.md` |
| AU-4 / AU-9 | Log storage / protection | Logs go to the host syslog/journald, so host retention, protection, and forwarding apply. monfile keeps no separate log files. | DESIGN §10 |
| AU-12 | Audit generation | Every state transition and action outcome emits an event | REQUIREMENTS AR-01 |
| CM-2 / CM-6 | Baseline / config settings | Config is root-owned 0640, strictly validated, and its hash is logged at load | DESIGN §5, `CONFIG_LOADED` |
| CM-7 | Least functionality | No network listeners, no shell, built-in actions only. Folder batches may run one root-owned program per folder (no shell, checked before each run, timeout, output logged). | ADR-0011, SR-13, `RestrictAddressFamilies=AF_UNIX` |
| CM-8 / SA-22 / RA-5 | Component inventory / vuln scanning | Third-party inventory with CPEs and a review log | `THIRD_PARTY.md` |
| CM-11 | User-installed software | Installed under `/opt/monfile`, with an inventory and checksums. Install/upgrade/uninstall steps are logged to syslog (`monfile-install`). | FILE_INVENTORY, `SHA256SUMS`, `MANIFEST.sha256`, ADR-0010 |
| SC-13 | Cryptographic protection | SHA-256 only (FIPS 180-4). Works with FIPS mode enabled. | ADR-0007 |
| SI-7 | Software/information integrity | Transfer integrity via SHA-256 verify. Code integrity via per-file manifest verification at install, root-owned install, AIDE, and fapolicyd trust | ADR-0007, ADR-0010, below |
| SI-11 | Error handling | Errors are logged without file contents, and failures don't destroy the source | ADR-0005, SR-11 |

## Host-owner checklist (per target host)

### All hosts
- [ ] Python ≥ 3.11 is installed from vendor repos (`python3.11`, or `python3` on Ubuntu 24.04)
- [ ] The `monfile` account was created by install.sh and has no login shell
- [ ] The install is on record: `journalctl -t monfile-install`, and `/opt/monfile/VERSION` records the tarball SHA-256 (`--tarball-sha`)
- [ ] Watch, destination, and processed dirs exist, with the `monfile` group granted access (rwx on watch dirs if disposition is move/delete)
- [ ] The `paths.conf` systemd drop-in lists those dirs in `ReadWritePaths=`
- [ ] `/etc/monfile/monfile.toml` is `root:monfile 0640`, and `monfile validate-config` passes
- [ ] **Only one** of `monfile.service` or `monfile-scan.timer` is enabled
- [ ] syslog forwarding to the SIEM includes facility/ident `monfile`
- [ ] SIEM alert rules exist for `ACTION_FAIL`, `INTEGRITY_MISMATCH`, `DISPOSITION_FAIL`, `PERMISSION_DENIED` and `CONFIG_INVALID` (monfile has no other alert channel, DESIGN §13 Q13)
- [ ] AIDE (or equivalent) covers `/opt/monfile` and `/etc/monfile`. Re-baseline after install or upgrade.
- [ ] If FIPS is enabled, `SERVICE_START` reports `fips=true`

### Hosts with folder batches (`trigger = "folder"`, ADR-0011)
- [ ] Each `command` program is approved, installed root-owned and not group/world-writable (e.g. `/usr/local/libexec/`, `root:root 0755`), and `validate-config` passes
- [ ] The program's SHA-256 (`COMMAND_START sha256=`) matches the approved version; AIDE covers the program
- [ ] Any sandbox loosening for the program (`monfile.service.d/command.conf`) is documented and approved: which directives, and why
- [ ] Any cron job that ran the program before is removed
- [ ] SIEM alert rules also cover `COMMAND_FAIL`, `COMMAND_TIMEOUT`, `COMMAND_REFUSED`, and `BATCH_INCOMPLETE`
- [ ] If installed with `--run-as`, the account and the trade-off (it can alter local state and manifests; syslog is authoritative) are accepted in the host's authorization
- [ ] On fapolicyd hosts, the program and its interpreter (e.g. `/bin/bash`) are trusted

### RHEL 8/9
- [ ] SELinux is enforcing. Watch, dest, and processed dirs have contexts that `unconfined_service_t` can access. Typically the default `var_t` or `default_t` labels are fine, but verify with `ausearch -m avc -c python3.11`.
- [ ] If **fapolicyd** is enabled, `/opt/monfile/` is trusted (`fapolicyd-cli --file list | grep /opt/monfile`). `install.sh` adds the trust automatically when fapolicyd is running. If fapolicyd was enabled later, re-run `install.sh`. Python source and bytecode loaded by the interpreter are checked against the trust DB.
- [ ] `python3.11` was installed from AppStream (RHEL 8) or AppStream/BaseOS (RHEL 9)

### Ubuntu 22.04/24.04
- [ ] On 22.04, `python3.11` is installed from universe (approved for this baseline, DESIGN §13 Q12)
- [ ] AppArmor: no profile is shipped in v1. The process runs unconfined by AppArmor but sandboxed by systemd.
- [ ] If Ubuntu Pro FIPS is enabled, confirm that `hashlib.sha256` works (it should)

## Known gaps / open items
- No confined SELinux domain or AppArmor profile in v1 (DESIGN §13 Q11)
- Release artifacts are checksummed but not yet GPG-signed (SECURITY.md)
- A `command` program is outside monfile's control: what it reads, writes, sends, and prints must be reviewed on its own (ADR-0011)
- With `--run-as` an interactive account, local state and manifests are not protected from that account
- State and manifests are kept indefinitely and never pruned by monfile (AR-08). The host owner must monitor `/var/lib/monfile` disk use and archive it per site retention policy.
