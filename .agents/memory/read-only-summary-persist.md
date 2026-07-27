---
name: Read-only paths must opt out of Risk Engine snapshot append
description: risk_api.summary() writes a daily snapshot by default; read-only layers must call summary(persist=False)
---

`risk_api.summary()` has a side effect: it appends a once-per-day snapshot to `risk_history.jsonl` when score+margin are available.

**Why:** Mission 1700 security review found that portfolio GET/export routes triggered this write via the default risk provider, violating the read-only guarantee. Fixed by adding `persist: bool = True` to `summary()` and calling `summary(persist=False)` from the portfolio service.

**How to apply:** Any new read-only/advisory layer that consumes Risk Engine data must use `summary(persist=False)` (or another non-writing accessor). Legacy callers (intelligence_service, automation, executive) intentionally keep the default writing behavior. A security test enforces the portfolio path (`test_mission1700_security_verification.py`).
