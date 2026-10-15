# monfile

Watches one or more folders on Linux hosts. When a file has been **unchanged for a settle window** (default 5 minutes), monfile runs a configurable workflow against it, such as copy or move, and records an auditable result.

Built for STIG-managed RHEL 8/9 and Ubuntu 22.04/24.04 hosts. Python 3.11+, standard library only.

> **Status:** 0.3.0. The core is implemented, unit-tested, and manually tested on Ubuntu 24.04: scanning, settle detection, copy/move with verification, dispositions, ready markers, audit events, manifests, and the CLI. It has not yet been tested on RHEL 8/9 or Ubuntu 22.04.

## Features
- Multiple watch folders, each with its own settle time, filters, recursion setting, and action chain
- Stability detection: size + mtime unchanged for the settle window, **and** a SHA-256 match at the start and end of the window
- Built-in actions: `copy` and `move`, with SHA-256 integrity verification of the delivered file
- Source disposition per folder: `leave`, `move` (to a processed dir), or `delete`
- Folder batches: wait until a whole folder has settled, then run one approved, root-owned program for the batch, with a timeout and every output line logged
- Audit events to syslog/journald, plus optional per-file JSON manifests
- Runs as a systemd daemon (`monfile run`) or a systemd timer/cron job (`monfile scan --once`)

## Quick start (development)
```sh
PYTHONPATH=src python3 -m monfile --help
PYTHONPATH=src python3 -m monfile validate-config --schema-only -c config/monfile.example.toml
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Documentation
| Doc | Purpose |
|---|---|
| [DESIGN.md](DESIGN.md) | Architecture, algorithms, config schema, open questions |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | Numbered, traceable requirements |
| [HOWTO.md](HOWTO.md) | Step-by-step setup and everyday use |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Install, configure, operate, troubleshoot |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Dependency / CVE tracking inventory |
| [SECURITY.md](SECURITY.md) | Security posture and vulnerability handling |
| [docs/audit/](docs/audit/) | Audit event catalog, STIG considerations, installed-file inventory |
| [docs/adr/](docs/adr/) | Architecture Decision Records |
| [AGENTS.md](AGENTS.md) | Contributor and AI-agent rules |

## License
[MIT](LICENSE)
