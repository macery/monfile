#!/usr/bin/env bash
# monfile uninstaller — DRAFT. Preserves config, state, and manifests (audit data).
#
# Usage: sudo ./packaging/uninstall.sh [--purge]   (--purge also removes the monfile user/group)
set -euo pipefail

# Steps (to implement):
#  1. `systemctl disable --now monfile.service monfile-scan.timer` (ignore errors if absent).
#  2. Remove the units, their *.d drop-ins, and /etc/tmpfiles.d/monfile.conf; `systemctl daemon-reload`.
#  3. If fapolicyd trust was added: `fapolicyd-cli --file delete /opt/monfile/ && fapolicyd-cli --update`.
#  4. `rm -rf /opt/monfile`.
#  5. KEEP /etc/monfile and /var/lib/monfile, and print their locations for manual removal.
#  6. --purge: `userdel monfile; groupdel monfile` (only if it owns no remaining files).

echo "uninstall.sh: draft only — not implemented yet (see step list in this file)" >&2
exit 1
