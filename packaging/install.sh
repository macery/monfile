#!/usr/bin/env bash
# monfile installer — DRAFT (ADR-0008). Every path touched must be listed in
# docs/audit/FILE_INVENTORY.md. Run as root from the unpacked tarball root.
#
# Usage: sudo ./packaging/install.sh [--fapolicyd] [--python /usr/bin/python3.11]
set -euo pipefail

PREFIX=/opt/monfile
CONF_DIR=/etc/monfile
UNIT_DIR=/etc/systemd/system

# Steps (to implement):
#  1. Preflight: require root; detect OS (/etc/os-release); locate python >= 3.11
#     (python3.12, python3.11, python3 in that order, or --python); verify it with
#     `"$PY" -I -c 'import sys; assert sys.version_info >= (3, 11)'`.
#  2. Integrity: the operator verifies the tarball with `sha256sum -c SHA256SUMS` BEFORE
#     extracting (OPERATIONS §2). Record that tarball hash in $PREFIX/VERSION (--tarball-sha).
#  3. Account: `groupadd -r monfile`; `useradd -r -g monfile -s /sbin/nologin -M -d / monfile`
#     (skip if they exist).
#  4. Code: `install -d -o root -g root -m 0755 $PREFIX/{bin,lib}`; copy src/monfile →
#     $PREFIX/lib/monfile (files 0644, dirs 0755); copy docs/ → $PREFIX/docs; write $PREFIX/VERSION.
#  5. Bytecode: `"$PY" -I -m compileall -q $PREFIX/lib` (the service can't write to /opt).
#  6. Launcher: write $PREFIX/bin/monfile (0755) with shebang `#!$PY -I`, which inserts
#     $PREFIX/lib into sys.path and calls monfile.cli.main().
#  7. Config: `install -d -o root -g monfile -m 0750 $CONF_DIR`; copy the example to
#     $CONF_DIR/monfile.toml (root:monfile 0640) ONLY if it is absent.
#  8. systemd: install the units (0644) and tmpfiles.d/monfile.conf; `systemd-tmpfiles --create`;
#     `systemctl daemon-reload`. Do NOT enable a run mode, because the admin picks one (OPERATIONS §4).
#  9. --fapolicyd (RHEL): `fapolicyd-cli --file add $PREFIX/ && fapolicyd-cli --update`.
# 10. Print the next steps: edit the config, add the ReadWritePaths drop-in, validate-config,
#     enable ONE mode, and re-baseline AIDE.

echo "install.sh: draft only — not implemented yet (see step list in this file)" >&2
exit 1
