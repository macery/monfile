#!/usr/bin/env bash
# Build the delivery tarball and its SHA256SUMS (ADR-0008).
# Usage: ./packaging/build-tarball.sh            → dist/monfile-<version>.tar.gz + dist/SHA256SUMS
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/src/monfile/__init__.py")"
name="monfile-${version}"
dist="$root/dist"

mkdir -p "$dist"
tar -C "$root" \
    --exclude='__pycache__' --exclude='*.pyc' \
    --owner=0 --group=0 --numeric-owner --sort=name \
    --transform "s,^,${name}/," \
    -czf "$dist/${name}.tar.gz" \
    src config packaging docs LICENSE README.md DESIGN.md THIRD_PARTY.md SECURITY.md CHANGELOG.md

(cd "$dist" && sha256sum "${name}.tar.gz" > SHA256SUMS)
echo "built $dist/${name}.tar.gz"
cat "$dist/SHA256SUMS"
