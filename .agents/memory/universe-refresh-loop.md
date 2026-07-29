---
name: Universe refresh design
description: Universe refresh is now scheduler-driven (scheduled_refresh, mode-independent); status must come from smart_config['scheduler_refresh'], not the change log.
---
Universe expansion history: originally only a background loop (`start_auto_loop`: 60s tick, 6h interval) that applied changes ONLY in `OTOMATIK` mode — so in ONERI/MANUEL a completed analysis looked like NOT_RUN_YET forever (observed on real Windows). The FIX ANALYSIS SCHEDULER mission replaced this: the scan scheduler cycle now calls `scheduled_refresh()` which applies suggestions REGARDLESS of mode and records `smart_config['scheduler_refresh'] = {last_result: COMPLETED|FAILED, last_error_code}`.

**Rules:**
- Canonical status source is `scheduler_refresh` in smart_config — never derive "has the universe scan run?" from `smart_changes.json` (it only records applied changes) and never let panel-triggered analyses (`last_analysis_time`) mask NOT_RUN_YET.
- smart_log is newest-first (`insert(0)`): "latest entry" is index 0, not -1.
- Honest reason codes: NOT_RUN_YET → INSUFFICIENT_ELIGIBLE_SYMBOLS / FILTERS_EXCLUDED_ALL / UNIVERSE_REFRESH_FAILED; keep `tools/windows/verify_scheduler.py` HONEST_REASON_CODES in sync with `universe_reason_code()`.

**Why:** two tasks fixed the same false-NOT_RUN_YET symptom concurrently; the scheduler-driven design won at merge. Do not reintroduce mode-gated interim codes (e.g. AUTO_MODE_OFF) — they are unreachable and contradict the canonical source.
