# Security Policy

## Supported versions
| Version | Supported |
|---|---|
| 0.x (pre-release) | development only |

## Reporting a vulnerability
Report suspected vulnerabilities privately to the project maintainer, not in public issue trackers. Include:
- the affected version
- the host OS
- reproduction steps
- the impact

*(Fill in the contact or ticket queue for your organization.)*

## Security posture summary
- **Dependencies:**
  - Python standard library only. OS-vendor packages are tracked in [THIRD_PARTY.md](THIRD_PARTY.md).
- **Privilege:**
  - Runs as the unprivileged `monfile` user inside a hardened systemd sandbox.
  - `install.sh --run-as USER` can use an existing non-root account instead (ADR-0011). If that account is an interactive login, anyone who can log in as it can alter the state database and manifests; treat syslog forwarded off-host as the authoritative record on such hosts.
- **Execution:**
  - No shell execution, and no dynamic code loading from config. Actions are a fixed, built-in registry.
  - One exception, opt-in per folder: a folder batch's `command` action runs one program named in the config (ADR-0011). It runs without a shell, with fixed arguments, a fixed environment, and a timeout, and only if the program and every directory above it are root-owned and not writable by group or others, checked before every run. Its SHA-256 and all its output lines are logged. The program inherits the service sandbox; loosening that sandbox for it is a host-owner decision.
- **Filesystem:**
  - Symlinks are never followed.
  - Outputs are confined to the configured roots.
  - Writes are atomic: temp file, fsync, verify, rename.
- **Integrity:**
  - SHA-256 is verified for every delivered file. The algorithm is FIPS-approved.
- **Configuration:**
  - The config must be root-owned and not writable by group or others, or the service refuses to start.
  - The config hash is logged at load time.
- **Audit:**
  - Structured events go to syslog/journald.
  - Optional per-file manifests are available. See [docs/audit/](docs/audit/).

Details: [DESIGN.md §9](DESIGN.md#9-security-model), [docs/audit/STIG_CONSIDERATIONS.md](docs/audit/STIG_CONSIDERATIONS.md).

## Release integrity
Each release tarball is accompanied by a `SHA256SUMS` file. Verify it before installing:
```sh
sha256sum -c SHA256SUMS
```
The tarball also contains `MANIFEST.sha256`, with a SHA-256 for every file in it. `install.sh` verifies the extracted files against it in a private staging dir and refuses symlinks or unlisted files (ADR-0010). This detects corruption or edits made after extraction. Authenticity still rests on `SHA256SUMS`.
*(Optional, open item: GPG-sign the SHA256SUMS file.)*
