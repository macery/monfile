# ADR-0006: Built-in actions only; no external command execution in v1

- **Status:** Superseded by [ADR-0011](0011-folder-batches-command-action.md) (2026-10-15)
- **Date:** 2026-10-12

## Context
Future workflows may want to run scripts. Running arbitrary commands from config is a large attack surface and complicates STIG/fapolicyd approval.

## Decision
v1 supports only built-in Python actions in a fixed registry. New workflows are added as code (new `Action` subclasses) through normal change control. DESIGN §6.4 lists the mandatory controls for any future exec action, which will need its own ADR.

## Consequences
- \+ The config cannot cause code execution.
- − Every new workflow needs a code release.
