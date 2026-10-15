# AGENTS.md — Rules for AI agents and human contributors

`monfile` is a folder-monitoring service for **STIG-managed RHEL 8/9 and Ubuntu 22.04/24.04 hosts**.
Every change must keep the tool auditable and the dependency surface minimal.
Read `DESIGN.md` before changing behavior.

## Project at a glance
- **Language:** Python 3.11+, **standard library only** at runtime.
- **Layout:** the code is in `src/monfile/`, tests are in `tests/` (stdlib `unittest`), deployment files are in `packaging/`, and documentation is in `docs/`.
- **Config:** TOML is parsed with stdlib `tomllib`. `config/monfile.example.toml` is the reference.
- **Core flow:** scan → stability check (size + mtime + SHA-256 over a settle window) → action chain → source disposition → audit.

## Hard rules (do not break without an ADR)
1. **No third-party runtime dependencies.** No `pip install`, no vendored packages. If a dependency is truly needed, write an ADR in `docs/adr/` and add it to `THIRD_PARTY.md` *in the same change*.
2. **Any new tool, OS package, or library**, including dev/build-only ones, must be added to `THIRD_PARTY.md`.
3. **No shell execution.** Never use `os.system`, `eval`, `exec`, or `subprocess` with `shell=True`. `subprocess` is used **only** by the folder-batch `command` action (`actions/command.py`, `batch.py`), with an argument list and `shell=False`, to run a root-owned program named in the config (ADR-0011). No other code starts processes.
4. **No symlink following** in watch, destination, or processed paths. Use `follow_symlinks=False` and `O_NOFOLLOW` where applicable.
5. **FIPS-safe crypto only:** SHA-256 via `hashlib.sha256()`. Never use MD5 or SHA-1, even for non-security purposes. FIPS-mode hosts reject them.
6. **Every audit-relevant behavior emits an event** listed in `docs/audit/AUDIT_EVENTS.md`. When you add, rename, or remove an event, update that catalog in the same change.
7. **Writes are atomic:** write to a temp name in the destination dir, `fsync`, verify the hash, then `os.replace`. Never leave a partial file under its final name.
8. **Least privilege:** the code must run as the unprivileged `monfile` user (or the existing non-root account chosen with `install.sh --run-as`, ADR-0011). Do not add anything that requires root at runtime.
9. **Never log file contents.** Log paths, sizes, hashes, and outcomes only.

## When you change things
| You changed… | Also update… |
|---|---|
| Behavior or architecture | `DESIGN.md`, plus an ADR in `docs/adr/` if it's a decision |
| A requirement | `docs/REQUIREMENTS.md` (keep the IDs stable, and never reuse a retired ID) |
| An installed file, path, owner, or mode | `docs/audit/FILE_INVENTORY.md` and `packaging/` |
| The config schema | `config/monfile.example.toml` and DESIGN §5 |
| A dependency or tool | `THIRD_PARTY.md` |
| Anything user-visible | `CHANGELOG.md` (the Unreleased section) |
| A security posture item | `SECURITY.md` and `docs/audit/STIG_CONSIDERATIONS.md` |

## Commands
```sh
PYTHONPATH=src python3 -m monfile --help                 # CLI
PYTHONPATH=src python3 -m unittest discover -s tests     # tests
PYTHONPATH=src python3 -m monfile validate-config --schema-only -c config/monfile.example.toml
```

## Style
- Use type hints everywhere and `from __future__ import annotations`.
- Use `pathlib.Path` for paths.
- Keep modules small and single-purpose (see DESIGN §3).
- New actions subclass `monfile.actions.Action` and register in `monfile/actions/__init__.py`.
- Each action must be idempotent and support `dry_run`.
- Tests must not touch real system paths. Use `tempfile.TemporaryDirectory()`.

## Open design items
See DESIGN §13. Do not silently resolve an open question in code. Record the decision there, or in an ADR, first.
