# ADR-0001: Python 3.11+, standard library only

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
Targets are STIG-managed RHEL 8/9 and Ubuntu 22.04/24.04 hosts. Every third-party library adds CVE-tracking load, approval paperwork, and offline-install complexity. The workflows (filesystem I/O, hashing, logging, config) are covered well by the Python stdlib.

## Decision
Implement in Python ≥ 3.11 using **only the standard library** at runtime. 3.11 is the minimum because `tomllib` is in the stdlib from that version. The interpreter comes from OS vendor repos (`python3.11` on RHEL 8/9 and Ubuntu 22.04; `python3` 3.12 on Ubuntu 24.04).

## Consequences
- \+ CVE surface is limited to OS packages already patched by the host process (THIRD_PARTY.md).
- \+ No pip and no vendoring, so the tarball contains only our code.
- − RHEL 8, RHEL 9, and Ubuntu 22.04 need a non-default python package installed.
- − No inotify bindings (stdlib has none), so polling is used instead (ADR-0002).
