# ADR-0005: Failure handling: log and skip

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
Actions can fail (destination full, permissions, integrity mismatch). The options were retry-forever, retry-then-quarantine, or log-and-skip.

## Decision
On failure: emit an `ACTION_FAIL` event, mark the file `FAILED` in state (with its stat and hash), and stop its chain. No disposition is applied, so the source stays in place. The file is retried only when its stat changes (a new version) or when an operator runs `monfile retry`. Completed actions are skipped on retry (DESIGN §6.2).

## Consequences
- \+ Predictable, with no hidden retry loops or hammering of a broken destination.
- \+ Failures are visible in the SIEM, and the source is untouched for investigation.
- − Transient failures need operator action. Revisit if they prove common.
- Alerting is syslog/SIEM only. No email or other channel in monfile (DESIGN §13 Q13). Sites alert on `ACTION_FAIL`, `INTEGRITY_MISMATCH`, `DISPOSITION_FAIL` and `PERMISSION_DENIED` in their SIEM.
