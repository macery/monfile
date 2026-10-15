# Third-Party Component Inventory (CVE Tracking)

This file inventories **every** external component that monfile depends on, ships with, or uses to build.
Its purpose is CVE monitoring and audit evidence on STIG-managed hosts.

**Policy:**
- monfile has **zero third-party Python packages** (ADR-0001). Its runtime dependencies are OS-vendor packages that the host's normal patch process maintains.
- Any change to this list requires updating this file in the same commit (see AGENTS.md).

---

## 1. Runtime dependencies (present on target hosts)

| Component | Version constraint | Package (RHEL 8 / RHEL 9 / Ubuntu 22.04 / Ubuntu 24.04) | Purpose | CPE (base) | License | Used by | CVE / advisory feeds | Last reviewed |
|---|---|---|---|---|---|---|---|---|
| CPython | ≥ 3.11 | `python3.11` / `python3.11` or `python3.12` / `python3.11` / `python3` (3.12) | Interpreter + stdlib | `cpe:2.3:a:python:python` | PSF-2.0 | everything | NVD, RHSA, USN, python.org security | 2026-10-12 |
| SQLite (libsqlite3) | OS-provided | `sqlite-libs` / `sqlite-libs` / `libsqlite3-0` / `libsqlite3-0` | State DB via `sqlite3` module | `cpe:2.3:a:sqlite:sqlite` | Public domain | `state.py` | NVD, RHSA, USN | 2026-10-12 |
| OpenSSL (libcrypto) | OS-provided | `openssl-libs` / `openssl-libs` / `libssl3` / `libssl3t64` | SHA-256 backend for `hashlib` | `cpe:2.3:a:openssl:openssl` | Apache-2.0 (3.x) | `hashing.py` | NVD, RHSA, USN, openssl.org | 2026-10-12 |
| glibc | OS-provided | `glibc` / `glibc` / `libc6` / `libc6` | C runtime | `cpe:2.3:a:gnu:glibc` | LGPL-2.1+ | interpreter | NVD, RHSA, USN | 2026-10-12 |
| systemd | OS-provided | `systemd` (all) | Service and timer management, journald | `cpe:2.3:a:systemd_project:systemd` | LGPL-2.1+ | `packaging/systemd/*` | NVD, RHSA, USN | 2026-10-12 |
| rsyslog / journald | OS-provided | `rsyslog` / `systemd-journald` | Receives audit events on `/dev/log` | `cpe:2.3:a:rsyslog:rsyslog` | GPL-3.0 / LGPL | `audit.py` | NVD, RHSA, USN | 2026-10-12 |

### Python standard library modules used
These modules ship with CPython, so they are covered by the CPython row above. They are listed so reviewers can map CPython CVEs to their impact on monfile.

`abc, argparse, collections, collections.abc, contextlib, dataclasses, enum, errno, fcntl, fnmatch, hashlib, json, logging, logging.handlers, os, pathlib, pwd, re, secrets, signal, socket, sqlite3, stat, subprocess, sys, threading, time, tomllib, typing`
(`subprocess` is used only by the folder-batch `command` action, `shell=False`, ADR-0011.)
(tests only: `unittest, unittest.mock, tempfile, io, shutil, tarfile`; `tests/test_packaging.py` runs `bash -n` and `build-tarball.sh` without a shell)

> Keep this list in sync with `grep -rhoE "^(import|from) [a-z0-9_.]+" src/ | sort -u`.

## 2. Install-time tools (used by `install.sh` on target hosts)

| Tool | Package | Purpose | CVE feeds | Last reviewed |
|---|---|---|---|---|
| bash | `bash` | runs install/uninstall scripts | NVD, RHSA, USN | 2026-10-12 |
| coreutils (`install`, `sha256sum`, `chown`, `chmod`, `mktemp`, `mv`, `rm`, `stat`, `readlink`, `sort`, `comm`, `cut`, `date`, `sync`, `id`) | `coreutils` | file placement and checksum verification | NVD, RHSA, USN | 2026-10-14 |
| findutils (`find`) | `findutils` | manifest completeness check, mode normalization | NVD, RHSA, USN | 2026-10-14 |
| sed | `sed` | read the version and manifest paths | NVD, RHSA, USN | 2026-10-14 |
| util-linux (`logger`, `runuser`, `flock`) | `util-linux` | syslog record of install steps, smoke test as `monfile`, single-instance lock | NVD, RHSA, USN | 2026-10-14 |
| shadow-utils (`useradd`, `groupadd`, `userdel`, `groupdel`) | `shadow-utils` / `passwd` | create (and, with `uninstall.sh --purge`, remove) the service account | NVD, RHSA, USN | 2026-10-14 |
| systemd tools (`systemctl`, `systemd-tmpfiles`) | `systemd` | install, stop/start, and reload units; create `/run/monfile` | NVD, RHSA, USN | 2026-10-14 |
| tar / gzip | `tar`, `gzip` | unpack delivery tarball | NVD, RHSA, USN | 2026-10-12 |
| setfacl (admin setup, documented in HOWTO §4) | `acl` | grant the `monfile` group access to watch/destination folders; not run by monfile or install.sh | NVD, RHSA, USN | 2026-10-12 |
| fapolicyd-cli (RHEL, optional) | `fapolicyd` | add, refresh, and remove trust for `/opt/monfile` (automatic when fapolicyd is running) | NVD, RHSA | 2026-10-14 |

## 3. Build/development-only tools (never shipped to target hosts)

| Tool | Version | Purpose | Notes | Last reviewed |
|---|---|---|---|---|
| git | any current | version control | dev workstation | 2026-10-12 |
| tar / gzip / sha256sum / find / xargs / sed | OS (`tar`, `gzip`, `coreutils`, `findutils`, `sed`) | `build-tarball.sh`, including `MANIFEST.sha256` | dev/build host | 2026-10-14 |
| GitHub Actions runner | `ubuntu-24.04` and `ubuntu-22.04` images | release builds (`.github/workflows/release.yml`); tests on every push (`.github/workflows/test.yml`) | GitHub-hosted; image updated by GitHub | 2026-10-12 |
| actions/checkout | v7.0.1, pinned to commit `3d3c42e5aac5ba805825da76410c181273ba90b1` | checkout in the release and test workflows | update the pin deliberately; check GitHub advisories | 2026-10-12 |
| python3.11 (CI only) | Ubuntu 22.04 universe, via `apt-get` on the runner | test on the minimum supported Python | same package as on target hosts | 2026-10-15 |
| gh CLI | runner-provided | creates the GitHub release | GitHub-hosted runner tool | 2026-10-12 |
| setuptools *(optional)* | ≥ 61 | `pip install -e .` local dev installs via pyproject.toml | not used by build-tarball.sh; never shipped | 2026-10-12 |
| ruff *(optional)* | pin when adopted | lint/format | not yet adopted; add a pin + review date when used | — |
| mypy *(optional)* | pin when adopted | type checking | not yet adopted | — |

## 4. Review procedure
1. **Cadence:** review monthly, before every release, and whenever a critical CVE is announced for any row above.
2. **Sources:**
   - NVD: https://nvd.nist.gov/vuln/search (search by CPE)
   - Red Hat: https://access.redhat.com/security/security-updates/ (RHSA)
   - Ubuntu: https://ubuntu.com/security/notices (USN)
   - Python: https://www.python.org/news/security/
3. **For each row:** check for new CVEs that affect the target versions. Assess reachability from monfile's usage (e.g. a `tarfile` CVE is unreachable, because monfile doesn't import it). Record the result below.
4. Update the *Last reviewed* dates.
5. Remediation is normally by the host's OS patching. If a CVE is reachable and unpatched, open an issue and note the mitigation here.

## 5. Review log
| Date | Reviewer | Scope | Findings | Action |
|---|---|---|---|---|
| 2026-10-12 | (initial) | inventory created | n/a — no code yet | — |
