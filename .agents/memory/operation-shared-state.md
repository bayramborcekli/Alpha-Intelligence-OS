---
name: Operation Control shared state store
description: Cross-worker consistency rule for Operation Control mutable state
---

Operation Control service state (automation, symbol states, idempotency, audit ring, stop-new-entries) is shared across gunicorn workers via a flock-guarded atomic JSON snapshot store (`operation_control_store.py`, snapshot at `alpha20_v1/operation_control_state.json`).

**Why:** With 2 sync workers, process-local state let the same idempotency key be accepted twice and made operator-visible state diverge per worker (former known limit #6).

**How to apply:** Any NEW mutable field added to the operation service must go through `_dump_shared`/`_load_shared` and its public method must carry `@_shared_mutation` (or `@_shared_view` for reads); otherwise it silently regresses to per-worker state. Corrupt snapshots raise sterile `STATE_STORE_CORRUPT` — never silently reset (idempotency safety). The store lock is re-entrant per thread; nested service calls are safe.
