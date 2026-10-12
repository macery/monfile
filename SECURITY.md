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
- **Execution:**
  - No shell or external command execution.
  - No dynamic code loading from config. Actions are a fixed, built-in registry.
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
*(Optional, open item: GPG-sign the SHA256SUMS file.)*
