# ADR-0010: Installer verification, upgrade, and fapolicyd behavior

- **Status:** Accepted
- **Date:** 2026-10-14
- **Amends:** ADR-0008

## Context
ADR-0008 says `install.sh` "verifies checksums," but the only checksum shipped was `SHA256SUMS` for the tarball itself, which can't be checked from inside the extracted tree. The installer also had to define what happens on an upgrade while monfile is running, and how fapolicyd trust is handled: if trust is missing, the service fails on hosts that run fapolicyd.

## Decision
1. **Per-file manifest.** `build-tarball.sh` writes `MANIFEST.sha256` (a SHA-256 for every file) into the tarball. `install.sh` refuses to run without it and refuses a tree that contains symlinks or files not in the manifest. It copies the listed files into a private, root-owned staging dir (`mktemp -d`, mode 0700) and runs `sha256sum --strict -c` **there**, so nothing can change between the check and the install. The operator's `sha256sum -c SHA256SUMS` on the tarball remains the trust anchor. `--tarball-sha` records that hash in `/opt/monfile/VERSION`.
2. **Upgrade = build, stop, swap, restart.** The new tree is built at `/opt/monfile.new`. Active units (`monfile.service`, `monfile-scan.timer`) are stopped. `/opt/monfile` is renamed to `/opt/monfile.old`, the new tree is renamed into place, and the old tree is removed. After daemon-reload and a smoke test (`monfile --version` run as the `monfile` user), the units that were running are started again. Enablement is never changed. The config and state are never modified. A leftover `.new` or `.old` from an interrupted run is cleaned up, or restored, on the next run.
3. **fapolicyd is auto-detected.** If `fapolicyd.service` is active, trust for `/opt/monfile/` is replaced (delete, then add, then `fapolicyd-cli --update`). `--fapolicyd` forces this, and `--no-fapolicyd` skips it. Because fapolicyd denies Python opening untrusted `.py` files, trust is added before `compileall` and refreshed after it, so the new `__pycache__` files are covered too.
4. **Recorded and reviewable.** Every step goes to syslog (`logger -t monfile-install`) as well as stderr. `--dry-run` prints every change without making it. `uninstall.sh` shares these helpers (`packaging/lib/common.sh`).
5. **`--purge` needs an empty slate.** `uninstall.sh --purge` refuses to remove the account while `/etc/monfile` or `/var/lib/monfile` exist, since those are owned by the account and are kept as audit data.

## Consequences
- \+ Corruption, or edits made after extraction, are caught before anything is installed. Extra files planted in the tree are never copied.
- \+ An upgrade on a running host needs no manual stop/start. A failed build leaves the old tree intact.
- \+ fapolicyd hosts work out of the box, and the trust DB never keeps entries for removed files.
- − Installing from a git checkout is no longer possible. Run `build-tarball.sh` first.
- − The manifest proves consistency with the tarball, not authenticity. Authenticity still depends on the operator verifying `SHA256SUMS` (signing remains an open item, SECURITY.md).
- − The service is briefly down during an upgrade (the swap, bytecode compile, and smoke test).
