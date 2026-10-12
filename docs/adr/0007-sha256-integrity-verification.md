# ADR-0007: SHA-256 integrity verification on transfer

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
Delivered files must be provably identical to the settled source. Hosts may run in FIPS mode, where MD5 and SHA-1 are blocked.

## Decision
Use SHA-256 (`hashlib.sha256`) for everything. During copy, the stream hash must equal the settled `hash_end`. After `fsync`, the destination temp file is re-read and its hash must match before the atomic rename. Hashes are recorded in audit events and manifests.

## Consequences
- \+ End-to-end integrity evidence, FIPS-compatible.
- − Additional I/O, at one extra read of each destination file.
