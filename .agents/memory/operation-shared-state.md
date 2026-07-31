---
name: Operation Control shared state store
description: Cross-worker consistency rule for Operation Control mutable state
---

Operation Control service state (automation, symbol states, idempotency, audit ring, stop-new-entries) is shared across gunicorn workers via a flock-guarded atomic JSON snapshot store (`operation_control_store.py`, snapshot at `alpha20_v1/operation_control_state.json`).

**Why:** With 2 sync workers, process-local state let the same idempotency key be accepted twice and made operator-visible state diverge per worker (former known limit #6).

**How to apply:** Any NEW mutable field added to the operation service must go through `_dump_shared`/`_load_shared` and its public method must carry `@_shared_mutation` (or `@_shared_view` for reads); otherwise it silently regresses to per-worker state. Corrupt snapshots raise sterile `STATE_STORE_CORRUPT` — never silently reset (idempotency safety). The store lock is re-entrant per thread; nested service calls are safe.

**Ek (2026-07-31, Görev 116):** Scheduler durumu da aynı sınıfa girdi: auto_controller._last_status process-local; sahibi worker artık her _update_status'ta git-dışı `controller_status_runtime.json`'a atomik snapshot yazar. scheduler_status(None) yerel running=False iken YALNIZ sahibi-canlı (os.kill pid,0) + taze (max(3×interval,900sn)) snapshot'a düşer; açık controller_status geçen testler fallback'e uğramaz. /api/paper/state artık readiness(None) çağırır — worker-yerel durumu asla doğrudan geçirme. Canlı etki restart gerektirir (sahibi worker snapshot yazmaya restart sonrası başlar).
