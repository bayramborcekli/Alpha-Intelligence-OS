---
name: Real-signal replay sizing
description: How to size data windows and structure variant runs for real-signal replay harnesses
---
Rule: with the production signal path (score >= 65, 3 symbols, 15m), real-signal replay produces roughly 1,200 closed trades per ~6 months of data (~0.065/candle across 3 symbols). Size PAGES to the trade target; a 2.7-year window produced 2,400+ trades and the process died silently (OOM, no traceback in log — stdout was also block-buffered; always run harnesses with `python -u`).

For parameter-variant experiments (e.g. fee_safety_factor A/B): precompute the signal stream ONCE (scores are independent of balance and of the parameter under test), then replay each variant from fresh state over the same stream. Prove determinism with a trade-level sha256 over (symbol, side, entry, exit, qty, pnl, reason), not aggregate metrics.

**Why:** Mission 1200 first attempt died mid-run with zero diagnostics; second attempt with a right-sized window finished in ~2 min. The identical-parameter variant matching the baseline hash is the reproducibility proof reviewers ask for.

**How to apply:** any future missionNNNN harness using the real signal filter or comparing config variants.
