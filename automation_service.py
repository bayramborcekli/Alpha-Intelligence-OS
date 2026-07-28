"""Mission 1600 — Automation Service Layer (Agent 03).

Automation Core (``automation_engine``) ile mevcut Intelligence Engine
arasındaki bağlantı katmanı:

    Automation Core → Automation Service → IntelligenceService
                                              → Risk Engine (mevcut)
                                              → Recommendation (mevcut)
                                              → Insight (mevcut)

Sorumluluklar:
- Automation isteğini alır, mevcut ``IntelligenceService.get_summary``
  sözleşmesiyle çalışmayı başlatır (yeni hesaplama algoritması yok).
- Sonucu timeline'ın kabul ettiği alanlara normalize eder.
- Sterile davranır: exception dışarı TAŞINMAZ; standart durumlar
  (OK / PARTIAL / UNAVAILABLE / FAILED) döner.
- Snapshot YAZMAZ — ``append_snapshot`` kararı ve çağrısı yalnız
  Automation Core'dadır.

Bu modül REST endpoint, UI, export, scheduler startup veya gunicorn
entegrasyonu içermez (Agent 04+ kapsamı).
"""

from __future__ import annotations

from typing import Any, Callable

import automation_engine
import intelligence_service

# Timeline ALLOWED_FIELDS ile birebir hizalı normalize alanları
_SNAPSHOT_FIELDS = (
    "generated_at", "status", "partial", "freshness", "insights",
    "recommendations", "warnings", "portfolio_summary", "risk_summary",
    "risk_explanations",
)

# Mevcut Intelligence sözleşmesindeki durumlar korunur; Automation
# Core yalnız OK/PARTIAL kaydeder (STALE/UNAVAILABLE kaydedilmez).
STATUS_OK = "OK"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_FAILED = "FAILED"

_service_factory: Callable[[], Any] | None = None


def _default_service() -> Any:
    """Gerçek IntelligenceService'i mevcut sağlayıcılarla kurar."""
    import risk_api
    # Spot-only: global_account / global_positions kaldırıldı;
    # varsayılan IntelligenceService sağlayıcıları kullanılır.
    return intelligence_service.IntelligenceService(
        risk_provider=risk_api.summary,
        alerts_provider=risk_api.alerts,
    )


def normalize_summary(payload: Any) -> dict:
    """Intelligence çıktısını timeline-uyumlu snapshot'a normalize eder.

    Deterministiktir: yalnız beyaz-listedeki alanları, girdi değerine
    dokunmadan (Decimal-string korunur) taşır. Geçersiz/eksik yapı →
    UNAVAILABLE snapshot (Automation Core bunu kaydetmez).
    """
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return {"status": STATUS_UNAVAILABLE, "advisory_only": True}
    status = payload.get("status")
    if status not in (STATUS_OK, STATUS_PARTIAL):
        # Mevcut sözleşmedeki diğer durumlar (ör. STALE/UNAVAILABLE)
        # olduğu gibi taşınır; kayıt kararı Automation Core'dadır.
        status = status if isinstance(status, str) else STATUS_UNAVAILABLE
    snapshot = {"advisory_only": True}
    for field in _SNAPSHOT_FIELDS:
        snapshot[field] = payload.get(field)
    snapshot["status"] = status
    return snapshot


def execute_intelligence_run(service: Any | None = None) -> dict:
    """Tek Intelligence çalışması yürütür; asla exception fırlatmaz.

    Dönen snapshot Automation Core'un ``summary_provider`` sözleşmesine
    uygundur: core, ``status`` OK/PARTIAL ise ``append_snapshot`` çağırır.
    """
    try:
        svc = service
        if svc is None:
            svc = (_service_factory or _default_service)()
        return normalize_summary(svc.get_summary())
    except Exception:
        # Sterile: exception metni/yolu asla taşınmaz.
        return {"status": STATUS_FAILED, "advisory_only": True}


def build_summary_provider(service: Any | None = None) -> Callable[[], dict]:
    """Automation Core'a verilecek sağlayıcıyı üretir."""
    return lambda: execute_intelligence_run(service)


def run_automation(*, service: Any | None = None,
                   config: dict | None = None,
                   state_path=None, history_path=None,
                   force: bool = False) -> dict:
    """Servis katmanı üzerinden tek kontrollü otomasyon koşusu.

    Kayıt (append_snapshot), kilit, durum makinesi ve retry yasağı
    tamamen Automation Core'dadır; bu katman yalnız yürütmeyi bağlar.
    """
    return automation_engine.run_once(
        build_summary_provider(service), config=config,
        state_path=state_path, history_path=history_path, force=force)


def automation_scheduler_tick(*, service: Any | None = None,
                              config: dict | None = None,
                              state_path=None, history_path=None,
                              now_epoch: float | None = None) -> dict:
    """Zamanlayıcı vuruşu için servis-bağlı sarmalayıcı (Agent 04 kullanır)."""
    return automation_engine.scheduler_tick(
        build_summary_provider(service), config=config,
        state_path=state_path, history_path=history_path,
        now_epoch=now_epoch)
