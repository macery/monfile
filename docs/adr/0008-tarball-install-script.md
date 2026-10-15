# ADR-0008: Delivery as tarball + install script

- **Status:** Accepted
- **Date:** 2026-10-12
- **Amended by:** ADR-0010 (manifest verification, upgrade, fapolicyd)

## Context
Native RPM/DEB packaging is heavier to build and sign. The tool is small and pure Python.

## Decision
Ship `monfile-<version>.tar.gz` with a `SHA256SUMS` file. `install.sh` does the following:
1. Verify checksums (per-file `MANIFEST.sha256`, see ADR-0010).
2. Create the `monfile` system user and group.
3. Install into `/opt/monfile` (root-owned) and precompile bytecode.
4. Install the systemd units, tmpfiles, and an example config.
5. Optionally add fapolicyd trust.

Releases are built by `.github/workflows/release.yml` when a `v*` tag is pushed, and published with `SHA256SUMS` as GitHub release assets.

Every installed path is listed in `docs/audit/FILE_INVENTORY.md`.

## Consequences
- \+ Simple to build and review.
- − The OS package manager doesn't track the installed files, so the file inventory and uninstall.sh have to compensate. Revisit RPM/DEB if fleet size grows.
