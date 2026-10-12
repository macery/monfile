# ADR-0003: Support daemon and timer modes with SQLite state

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
It is undecided whether hosts will run monfile as a resident service or a periodic job.

## Decision
One code path with two entry points: `monfile run` (loop) and `monfile scan --once` (one cycle). All state lives in SQLite (stdlib `sqlite3`), so settle windows span runs. An `fcntl.flock` lock prevents concurrent instances.

## Consequences
- \+ The deployment choice can be made per host without code changes.
- \+ State survives restarts and reboots.
- − Both systemd unit sets must be maintained, and operators must enable only one.
