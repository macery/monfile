# ADR-0004: TOML configuration via stdlib tomllib

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
The config needs nested per-folder settings, action chains, and admin comments, with no third-party parser.

## Decision
Use TOML at `/etc/monfile/monfile.toml`, parsed with `tomllib`. Validation is strict: unknown keys are errors. The file must be root-owned and not writable by group or others.

## Consequences
- \+ Readable, commentable, and needs no dependency.
- − `tomllib` is read-only, so the tool can never rewrite its own config. That is acceptable, and even desirable for audit.
