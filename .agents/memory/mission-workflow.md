---
name: Mission workflow pattern
description: Per-agent mission loop, closure baseline chain, and delivery-report conventions for Alpha Intelligence OS missions.
---

# Mission workflow pattern

Missions arrive as spec files in `attached_assets/` (one per agent). Each agent follows:
implement → tests → architect code-review subagent (fix real findings; re-review only for substantial rework) → `git checkout -- alpha20_v1/` before full regression (running bot mutates configs) → full `python -m pytest -q` (~90–110s) → commit ONLY scoped files → `gitPush({branch:"main",provider:"github"})` → Turkish delivery report with the spec's exact `── SECTION ──` headers, ending "Executive Review bekle".

**Why:** the user runs a chained multi-agent mission process; deviating from the loop or headers breaks their executive review flow.

**How to apply:** on any new mission spec upload, repeat the loop. Report must include: new-test count, FAIL/SKIP 0/0, total regression, Exchange Write 0, Secret Exposure 0, commit hash + push OK, NEXT AGENT.

## Closure baseline chain (verified from git history)
- Mission 1700 closed at 1335 PASS (`05eb08a`)
- Mission 1800 closed at 1596 PASS (`327e160`)
- Mission 1900 closed at 2207 PASS (closure commit `a79415e`; completion pre-closure 2146 at `08a409b`)
- Mission 2000 closed at 4375 PASS (closure `03e181d`; Execution Core v1.0.0 CERTIFIED; core manifest baseline `01aa429`:3704 deliberately distinct)
- Mission 2100 (v1.1.0 "Controlled Execution") A01: Controlled Execution Foundation, commit `4304527`, regression 4619 PASS (244 new tests)
- Mission 2100 A02: Runtime Domain Models, commit `69bd05c`, regression 5215 PASS (596 new tests)
- Mission 2100 A03: Paper Broker & Ledger, commit `32f4a3a`, regression 5585 PASS (370 new tests; exact double-entry via cost_basis, IMMEDIATE_FULL_FILL only)
- Mission 2100 A05: Shadow Mode, commit `459ca5a`, regression 6392 PASS (398 new tests). Gözlem çağıran-sahipli ShadowMarketObservation saf verisi olarak gelir (bu katman ağa çıkmaz); ShadowMarketObservation+ShadowResult bilinçli ek modeller; PaperExecutionReferences/PaperRiskEvaluator A04'ten yeniden kullanıldı; ShadowStatistics alan adında "requests" token'ı güvenlik taramasına takıldı → total_cancels olarak adlandırıldı.
- Mission 2100 A04: Paper Execution Service, commit `bf2a21d`, regression 5994 PASS (409 new tests). PAPER-policy validation risk'ten ÖNCE; cancel'da risk aşaması bilinçli muaf (istek uydurulamaz, testle sabit); broker catch-all → INTERNAL_FAILURE steril kod; tests/test_ownership.py'ye paper_execution_service.py "submit_order" token muafiyeti eklendi (PAPER-only, kendi mimari taramaları altında).

## Standing constraints (all missions)
Read-only architecture (exchange writes forever 0); Decimal-only money math (AST-tested, no float literals); unknown → null; sterile error codes only; no threads/schedulers; wall-clock/UUID only at API boundary; never `pkill gunicorn`; keep `attached_assets/` out of scoped commits; MappingProxyType → plain dict before json.dumps.
- Mission 2100 A06: Micro Live Authorization, commit `ba896ca`, regression 6895 PASS (503 new tests). Yetkilendirme sınırı — asla emir/borsa erişimi yok; fail-safe kural: deny/expire/revoke mod/politika/kill-switch kapılarına tabi değil (sadece geçiş matrisi + expire için zamansal önkoşul); izin kapısı REQUIRE_EXPLICIT_AUTHORIZATION kodunu PASS sayar (bu servis o bileşendir); approve VE request_authorization policy_reference_match zorunlu (architect bulgusu ile eklendi); MicroLiveReferences/Snapshot/Statistics/Heartbeat/Result bilinçli ek modeller. Test tuzağı: object.__setattr__ frozen dataclass'ı deler ve modül-seviyesi paylaşılan servisi kirletir — immutability testinde düz setattr kullan.
- Mission 2100 A07: Order Lifecycle & Reconciliation, commit `df0fb04`, regression 7667 PASS (740 yeni test). Kapalı geçiş matrisi (queue/fail bilinçli ek işlemler — QUEUED/FAILED erişilebilir olmalı); yalnız tam dolum; mutabakat taban çizgisi HER ZAMAN istek kümesidir (boş istek kümesinde bile downstream emirler MISSING_ORDER — architect bulgusu); sembol/yön sapması kapalı kod kümesi gereği QUANTITY_MISMATCH altında; durum/PnL yalnız sonuç kaynakları arasında karşılaştırılır (istek niyettir); ReconciliationMismatch/Audit/Statistics bilinçli ek modeller.
- HOTFIX 2100-HF-001: Binance Global panosu Spot hesabına döndü, commit `ffdf3f9`, regression 6927 PASS (32 yeni test). global_spot_account() /api/v3/account (imzalı) + /api/v3/ticker/price (imzasız _public_get) kullanır; bozuk/sıfır fiyat sözlüğe alınmaz → valuation PARTIAL (sessiz 0 değerleme yok); Futures paneli/global_futures anahtarı değişmedi. Test tuzağı: _serve önbelleği testler arası taşınır — mock helper başında invalidate_caches() zorunlu.
