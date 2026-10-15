#!/usr/bin/env bash
# Build the delivery tarball and its SHA256SUMS (ADR-0008).
# The tarball also carries MANIFEST.sha256, a SHA-256 for every file in it, which install.sh
# verifies before installing anything (ADR-0010).
# Usage: ./packaging/build-tarball.sh            → dist/monfile-<version>.tar.gz + dist/SHA256SUMS
#        DIST=/some/dir ./packaging/build-tarball.sh   (output dir override, used by tests)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/src/monfile/__init__.py")"
name="monfile-${version}"
dist="${DIST:-$root/dist}"
contents=(src config packaging docs LICENSE README.md HOWTO.md DESIGN.md THIRD_PARTY.md SECURITY.md CHANGELOG.md)

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT

# install.sh refuses symlinks in the release tree, so refuse them here first.
links="$(cd "$root" && find "${contents[@]}" -type l)"
[[ -z $links ]] || { echo "symlinks are not allowed in the release: $links" >&2; exit 1; }

(cd "$root" && find "${contents[@]}" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0 \
    | LC_ALL=C sort -z | xargs -0 sha256sum) > "$work/MANIFEST.sha256"

mkdir -p "$dist"
tar --exclude='__pycache__' --exclude='*.pyc' \
    --owner=0 --group=0 --numeric-owner --sort=name \
    --transform "s,^,${name}/," \
    -czf "$dist/${name}.tar.gz" \
    -C "$root" "${contents[@]}" \
    -C "$work" MANIFEST.sha256

(cd "$dist" && sha256sum "${name}.tar.gz" > SHA256SUMS)
echo "built $dist/${name}.tar.gz ($(wc -l < "$work/MANIFEST.sha256") files in MANIFEST.sha256)"
cat "$dist/SHA256SUMS"
