# ADR-0002: Polling-based stability detection (size + mtime + SHA-256)

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
Files may arrive by local copy, SCP/SFTP, or onto NFS/CIFS mounts. inotify does not see writes made by remote NFS/CIFS clients, and the stdlib has no inotify binding (ADR-0001). The requirement is "unchanged for 5 minutes."

## Decision
Poll each folder every `scan_interval_seconds`. A file is READY when `(dev, ino, size, mtime_ns)` has been unchanged for `settle_seconds`, **and** (if `verify_hash`) the SHA-256 at the window start equals the one at the window end. The end hash becomes the source-of-truth hash for integrity verification. See DESIGN §4.

## Consequences
- \+ Works the same on local and network filesystems, and is immune to server clock skew.
- \+ Catches in-place rewrites that preserve size and mtime.
- − Extra reads per file. Mitigated by a per-folder `verify_hash = false` option.
- − Latency is at least `settle_seconds`, which is acceptable by requirement.
