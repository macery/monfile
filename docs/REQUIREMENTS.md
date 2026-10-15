# Requirements

The ID prefixes are:
- **FR:** functional
- **SR:** security
- **AR:** audit
- **OR:** operational/platform

IDs are permanent. A retired requirement is marked `RETIRED` and its ID is never reused.
The *Verify* column names how each requirement is (or will be) demonstrated: **T** = automated test, **I** = inspection, **D** = demonstration.

## Functional
| ID | Requirement | Design | Verify |
|---|---|---|---|
| FR-01 | Monitor one or more configured folders for new regular files. | §3, §5 | T |
| FR-02 | Each folder has independent `settle_seconds`, filters, recursion, manifest toggle, action chain, and disposition. | §5 | T |
| FR-03 | A file is eligible only after its size, mtime, and inode are unchanged for `settle_seconds` (default 300). | §4 | T |
| FR-04 | When `verify_hash` is enabled, the SHA-256 at the start of the settle window must equal the SHA-256 at the end. | §4 | T |
| FR-05 | Recursion into subdirectories is configurable per folder. | §5 | T |
| FR-06 | Include/exclude glob filters apply per folder. | §5 | T |
| FR-07 | Eligible files are processed by an ordered chain of built-in actions. | §6 | T |
| FR-08 | Provide a `copy` action. | §6.3 | T |
| FR-09 | Provide a `move` action. | §6.3 | T |
| FR-10 | Source disposition after success is configurable: `leave`, `move`, or `delete`. | §7 | T |
| FR-11 | On action failure, log the failure, mark the file FAILED, and do not retry until the file changes or an operator retries it. | §4.2, ADR-0005 | T |
| FR-12 | Destination name collisions are handled by policy: `suffix`, `skip`, or `fail`. | §7 | T |
| FR-13 | Support daemon mode (`run`) and one-shot mode (`scan --once`) using the same logic. | §11 | T, D |
| FR-14 | State persists across restarts, so settle windows and processed status survive. | §8 | T |
| FR-15 | Provide `validate-config`, `status`, `retry`, and `--dry-run`. | §11 | T |
| FR-16 | New action types can be added as Python plugins without changing the core. | §6.1 | I |
| FR-17 | A folder can require a ready-marker file (`<name><suffix>`) before a data file becomes eligible. The marker is removed after successful processing. | §4.4, ADR-0009 | T |
| FR-18 | A DONE file whose stat changes later is processed again as a new version. | §4.3 | T |
| FR-19 | Zero-byte files are processed by default. A per-folder `skip_empty` option skips them. | §4.3 | T |
| FR-20 | Files have no size limit. Files over 10 GiB emit a `FILE_LARGE` warning. | §4.3 | T |
| FR-21 | A folder can be turned off with `enabled = false` without removing it from the config. A disabled folder is not scanned and its paths are not checked, and its being disabled is logged at startup. | §5 | T |

## Security
| ID | Requirement | Design | Verify |
|---|---|---|---|
| SR-01 | Run as a dedicated unprivileged service account. Root is never required at runtime. | §9 | I, D |
| SR-02 | No third-party runtime libraries. Python standard library only. | ADR-0001 | I (import scan) |
| SR-03 | No shell or external command execution in v1. | §9, ADR-0006 | I, T |
| SR-04 | Never follow symlinks in watch, destination, or processed paths. | §9 | T |
| SR-05 | Use only FIPS-approved hashing (SHA-256). | §9 | I, T |
| SR-06 | Refuse to start if the config file is not root-owned, or is group/world-writable. | §5, §9 | T |
| SR-07 | Prevent path traversal. Outputs must resolve under their configured roots. | §9 | T |
| SR-08 | Deliver files atomically, so no partial file ever appears under its final name. | §6.3 | T |
| SR-09 | Verify the integrity of every delivered file by SHA-256 against the source. | §6.3, ADR-0007 | T |
| SR-10 | The systemd unit applies sandboxing and hardening directives. | §9, packaging | I |
| SR-11 | File contents are never written to logs or manifests. | §9 | I |
| SR-12 | Delivered files are created with mode `0640`. | §7 | T |

## Audit
| ID | Requirement | Design | Verify |
|---|---|---|---|
| AR-01 | Emit an audit event for service start and stop, config load (with the config SHA-256), file detection, readiness, each action result, disposition, and failure. | §10, AUDIT_EVENTS | T |
| AR-02 | Events go to the local syslog/journald so that host log forwarding applies. | §10.1 | D |
| AR-03 | Events use a stable `event=<ID>` key=value format that is documented in the catalog. | §10.1 | I |
| AR-04 | Per-file JSON manifests can be enabled or disabled per folder. | §10.2 | T |
| AR-05 | Manifests record the source hash, every action result and destination hash, and the disposition. | §10.2 | T |
| AR-06 | The repository maintains a third-party inventory for CVE tracking. | THIRD_PARTY.md | I |
| AR-07 | The repository maintains an installed-file inventory with owners and modes. | FILE_INVENTORY.md | I |
| AR-08 | monfile never prunes state records or deletes manifests. Retention is indefinite. | §8 | T, I |

## Operational / platform
| ID | Requirement | Design | Verify |
|---|---|---|---|
| OR-01 | Supports RHEL 8/9 and Ubuntu 22.04/24.04 with Python ≥ 3.11 from the OS vendor repos (on 22.04, universe). | §12 | D |
| OR-02 | Works with SELinux enforcing (RHEL) and AppArmor (Ubuntu). | §12 | D |
| OR-03 | Works on FIPS-mode hosts. | §9 | D |
| OR-04 | Delivered as a tarball with a SHA-256 checksum and an install script. | ADR-0008 | D |
| OR-05 | Only one instance runs per host at a time, enforced by a lock. | §8 | T |
| OR-06 | Config changes take effect only on restart. SIGHUP does not reload or stop the process. | §9 | T |
