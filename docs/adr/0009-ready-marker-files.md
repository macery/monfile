# ADR-0009: Optional ready-marker files

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
Some writers pause mid-transfer for longer than any reasonable settle window. Stat- and hash-based stability (ADR-0002) cannot tell such a pause from "done," so a partial file could be delivered. The host owner confirmed that marker-file support is needed in v1 (DESIGN §13 Q9).

## Decision
Add an optional per-folder `ready_marker` suffix (for example `.done`). When it is set, data file `<name>` becomes READY only when a regular, non-symlink file `<name><suffix>` exists in the same directory **and** the normal settle/hash rules pass. Markers are never processed as data, and their contents are never read. After a successful disposition, monfile deletes the marker. On failure it leaves the marker in place. Orphan markers are logged and left alone. Full rules are in DESIGN §4.4.

## Consequences
- \+ Writers that pause can no longer cause partial deliveries.
- \+ Keeping the settle check means a writer that creates the marker too early still can't cause a partial delivery.
- \+ Removing the marker on every disposition (including `leave`) means a rewritten file needs a fresh marker, so nothing is reprocessed by accident.
- − Marker folders need write access on the watch dir, even with `on_success = "leave"`.
- − Writers must follow the naming convention. A misnamed marker leaves the file waiting forever, which shows up in `status` and `MARKER_ORPHAN`.
