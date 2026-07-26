---
name: Fee dominance with tight stops
description: Why paper trades on 1m ATR stops always lose despite hitting TP
---
Rule: position size = risk_usdt / stop_distance, and fee = (entry+exit)×qty×0.001. When stop distance is tiny (e.g. 1m ATR), qty explodes and fees dwarf the ~35 USDT gross target — even TAKE_PROFIT closes end net-negative.

**Why:** Mission 10 replay (10 real-data closes) showed gross +217 vs fees 3940 USDT. Accounting was fully consistent — it's an economics property of the fee model, not a bug.

**How to apply:** Validation harnesses or backtests must use realistic (15m+) ATR stops, or add a min stop-distance / max notional guard before drawing conclusions from PnL. Never interpret "all trades lose" on tight stops as an accounting error.
