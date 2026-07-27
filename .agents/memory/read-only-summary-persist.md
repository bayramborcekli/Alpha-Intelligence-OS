---
name: Read-only paths must opt out of Risk Engine snapshot append
description: risk_api.summary() writes a daily snapshot by default; read-only layers must call summary(persist=False)
---

`risk_api.summary()` has a side effect: it appends a once-per-day snapshot to `risk_history.jsonl` when score+margin are available.

**Why:** Mission 1700 security review found that portfolio GET/export routes triggered this write via the default risk provider, violating the read-only guarantee. Fixed by adding `persist: bool = True` to `summary()` and calling `summary(persist=False)` from the portfolio service.

**How to apply:** Any new read-only/advisory layer that consumes Risk Engine data must use `summary(persist=False)` (or another non-writing accessor). Legacy callers (intelligence_service, automation, executive) intentionally keep the default writing behavior. A security test enforces the portfolio path (`test_mission1700_security_verification.py`).

**Update (2026-07-27, Mission 1800/07):** The default IntelligenceService risk provider also calls the risk summary with persist=True, so any read-only chain built on `IntelligenceService().get_snapshot()` silently wrote snapshots on GET. Fixed by constructing the service with `risk_provider=lambda: risk_api.summary(persist=False)` in the read-only default provider chain. Lesson: when spying for write-paths in tests, do NOT stub `IntelligenceService` itself — over-mocking hid this leak from the Mission 1700 security suite; keep the real internal call graph and only feed offline data sources.
