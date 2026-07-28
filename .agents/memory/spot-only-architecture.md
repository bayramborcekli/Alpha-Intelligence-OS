---
name: Spot-only architecture (Futures removed)
description: Binance Global Futures private surfaces are permanently removed; tombstone contract and deliberate keeps.
---
# Spot-only mimari

Rule: Binance Global private FUTURES API (fapi + creds) is permanently removed from the dashboard. Global account data comes only from Spot `/api/v3/account` (source label `BINANCE_GLOBAL_SPOT`).

**Tombstone contract:** `dashboard_api.global_account/global_positions/global_orders` remain as symbols but return a sterile model `{ok:False, status:"DISABLED", meta.freshness:"DISABLED", error.code:"FUTURES_REMOVED"}` — zero network, zero credentials. Advisory layers (intelligence/automation/risk/recommendation) rely on graceful not-ok degradation; do NOT delete these symbols or change the error code without sweeping those layers.

**Why:** deleting the symbols would force rewriting thousands of mission tests and every advisory provider; tombstones preserve the provider contract.

**Deliberate keeps (not remnants):**
- alpha20 / alpha20_v1 / tools use PUBLIC `/fapi` klines (no creds) — PAPER bot market data.
- `risk_api` KNOWN_EXCHANGES = `BINANCE_GLOBAL_FUTURES` is the PAPER simulator universe label (UI risk.html dropdown too).
- `accounts_registry` schema keeps `futures_enabled` field for data compat, but `futures_capable=False` for BINANCE_GLOBAL.
- `portfolio_api.positions_view/orders_view` are tombstone pass-throughs; `positions_csv/orders_csv` are dead code (follow-up: remove).

**Creds aliases:** `_global_creds()` tries BINANCE_API_KEY → BINANCE_API_Key → BINANCE_GLOBAL_API_KEY → BINANCE_GLOBAL_API_Key (secret analog). User renames secrets often; keep the alias chain.
