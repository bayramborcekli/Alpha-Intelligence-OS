"""Tek kanonik sistem orkestratörü — mevcut servisleri sıralar/izler.

Yeni market-data, risk veya karar motoru OLUŞTURMAZ; yalnız mevcut
kanonik bileşenleri tek başlangıçta bağlar ve readiness graph üretir:

MARKET DATA → UNIVERSE → ANALYSIS → FEATURES → DECISION ENGINE
→ RISK ENGINE → PAPER CONTROLLER

Kaynaklar (kanonik):
- Market data / analiz / karar: alpha20_v1 auto_controller döngüsü
  (alpha20.fetch_klines → decision_engine → adaptive_risk)
- Evren: alpha20_v1/universe_manager (BASE_SYMBOLS pinli, max 20)
- Tercihler: services/runtime_preferences (git dışı)
- Risk profili: services/risk_profiles (adaptive override ile GERÇEK
  sizing/limit girdisi; config.json'a yazılmaz)
- Otomasyon/sembol durumu: operation_control_state.json
- Güvenlik: services/emergency_stop her şeyin üzerinde

Genel durum kuralları:
- Analysis/controller hattı çalışmıyorsa GREEN OLMAZ.
- Risk Engine (profil + adaptive_risk) hazır değilse GREEN OLMAZ.
- Tek sembol/ikincil sorun → YELLOW; kritik hat kopuk → RED.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("system_orchestrator")

ROOT = Path(__file__).resolve().parent.parent
ALPHA_DIR = ROOT / "alpha20_v1"

_started: dict[str, Any] = {"at": None, "steps": []}


def _alpha_path() -> None:
    p = str(ALPHA_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def apply_user_preferences(ac_module) -> dict[str, Any]:
    """Kalıcı tercihlerini GERÇEK motorlara uygula (bellek-içi).

    - Risk profili → adaptive_risk limitleri (override)
    - scan interval → controller döngü aralığı (override)
    config.json'a yazılmaz; her başlangıçta yeniden uygulanır."""
    from services import risk_profiles as rp
    from services import runtime_preferences as prefs
    p = prefs.get_all()
    flags = rp.adaptive_flags(p["selected_risk_profile"])
    existing = dict(getattr(ac_module, "RUNTIME_ADAPTIVE_OVERRIDE",
                            {}) or {})
    existing.update(flags)
    ac_module.set_runtime_adaptive_override(existing)
    ac_module.set_runtime_scan_seconds(
        p["scan_interval_minutes"] * 60)
    log.info("Kullanıcı tercihleri uygulandı: profil=%s scan=%sdk",
             p["selected_risk_profile"], p["scan_interval_minutes"])
    return p


def start(app_module) -> dict[str, Any]:
    """Tek başlangıç: tercihleri uygula (controller'ın kendisini
    reconcile adımı başlatır — serve_windows). Adımları kaydeder."""
    steps: list[str] = []
    try:
        _alpha_path()
        import auto_controller as ac
        apply_user_preferences(ac)
        steps.append("PREFERENCES_APPLIED")
    except Exception as exc:
        log.error("Tercih uygulama hatası: %s", exc)
        steps.append(f"PREFERENCES_FAILED:{exc}")
    _started["at"] = datetime.now(timezone.utc).isoformat()
    _started["steps"] = steps
    return {"at": _started["at"], "steps": steps}


# ── Readiness ────────────────────────────────────────────────────────


def _tail_jsonl(path: Path, n: int = 50) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []


def recent_decisions(n: int = 50) -> list[dict]:
    return _tail_jsonl(ALPHA_DIR / "decisions.jsonl", n)


def scheduler_status(controller_status: dict[str, Any] | None = None,
                     preference: str | None = None) -> dict[str, Any]:
    """TEK KANONİK Analysis Scheduler durumu.

    Analiz zamanlayıcısı = auto_controller analiz döngüsü (fetch →
    features → decision → risk). Kaynaklar:
    - Tercih (istenen durum): services/runtime_preferences
      (analysis_scheduler, scan_interval_minutes — varsayılan 5 dk)
    - GERÇEK durum: auto_controller.get_status() (canlı thread)

    Tercih RUNNING olsa bile gerçek worker çalışmıyorsa running=False
    ve state=STARTUP_FAILED döner — UI tercihini gerçek çalışma
    durumu gibi GÖSTEREMEZ. Legacy ALPHA_AUTOMATION_ENABLED / 60 dk
    yolu intelligence RAPORLAMA zamanlayıcısıdır; analiz hattının
    doğruluk kaynağı DEĞİLDİR (yalnız /automation'da legacy blok)."""
    from services import runtime_preferences as prefs
    p = prefs.get_all()
    if controller_status is None:
        try:
            _alpha_path()
            import auto_controller as ac
            controller_status = ac.get_status()
        except Exception:
            controller_status = {}
    st = controller_status or {}
    # İstenen durum: öncelik kanonik operasyon tercihi (Operation
    # Control store — manuel STOP restart sonrası korunur); yoksa
    # runtime_preferences başlangıç varsayılanı. İkisi ayrı "duplicate
    # state" DEĞİLDİR: operation store kanoniktir, prefs yalnız
    # başlangıç varsayılanıdır.
    if preference not in ("RUNNING", "STOPPED"):
        preference = p["analysis_scheduler"]  # RUNNING | STOPPED
    enabled = preference == "RUNNING"
    running = bool(st.get("running"))
    interval_min = p["scan_interval_minutes"]
    last_run = st.get("last_cycle_time")
    last_error = st.get("last_cycle_error")
    next_run = None
    if running and last_run:
        try:
            next_run = datetime.fromtimestamp(
                datetime.fromisoformat(last_run).timestamp()
                + interval_min * 60, timezone.utc).isoformat()
        except ValueError:
            next_run = None
    if running:
        state = "RUNNING"
        last_result = ("FAIL" if last_error else
                       ("PASS" if last_run else "NOT_RUN_YET"))
    elif enabled:
        state = "STARTUP_FAILED"  # tercih RUNNING, worker yok
        last_result = "FAIL"
    else:
        state = "STOPPED"
        last_result = "STOPPED"
    return {
        "preference": preference,
        "enabled": enabled,
        "running": running,
        "state": state,
        "interval_minutes": interval_min,
        "last_run": last_run,
        "next_run": next_run,
        "active_run_id": st.get("cycle_count") or None,
        "last_result": last_result,
        "last_error": last_error,
        "analyzed_symbol_count": st.get("analyzed_symbol_count",
                                        None),
    }


def universe_reason_code(universe_size: int) -> str | None:
    """Evren temel 3 sembolde kaldıysa dürüst neden kodu üret.

    3 sembol 'başarılı dinamik evren' gibi gösterilmez. Kaynak:
    smart_config (scheduler_refresh + last_analysis_time +
    candidate_count) — ilk başarılı yenilemeden sonra NOT_RUN_YET
    kalıcı olarak temizlenir."""
    try:
        _alpha_path()
        import universe_manager as um
        base_n = len(um.BASE_SYMBOLS)
        if universe_size > base_n:
            return None  # gerçekten genişlemiş
        cfg = um.get_smart_config()
        sr = cfg.get("scheduler_refresh") or {}
        if sr.get("last_result") == "FAILED":
            return sr.get("last_error_code") or "UNIVERSE_REFRESH_FAILED"
        if not cfg.get("last_analysis_time"):
            return "NOT_RUN_YET"  # hiç uygun çevrim koşmadı
        if int(cfg.get("candidate_count") or 0) <= 0:
            return "INSUFFICIENT_ELIGIBLE_SYMBOLS"
        # Aday vardı ama evrene eklenmedi (filtre/limit/mod)
        return "FILTERS_EXCLUDED_ALL"
    except Exception:
        return "UNIVERSE_REFRESH_FAILED"


def universe_refresh_result() -> str:
    """Son scheduler-kaynaklı evren yenilemesinin sonucu:
    COMPLETED | FAILED | NOT_RUN_YET."""
    try:
        _alpha_path()
        import universe_manager as um
        sr = um.get_scheduler_refresh_status()
        return sr.get("last_result") or "NOT_RUN_YET"
    except Exception:
        return "NOT_RUN_YET"


def readiness(controller_status: dict[str, Any],
              automation_state: str,
              emergency_active: bool) -> dict[str, Any]:
    """Gerçek kaynaklardan readiness graph üret (ağ çağrısı yapmaz)."""
    from services import runtime_preferences as prefs
    _alpha_path()
    p = prefs.get_all()

    # Evren
    try:
        import universe_manager as um
        cfg = um.load_main_config()
        symbols = cfg.get("symbols") or []
        universe_size = len(symbols)
        universe_ready = universe_size >= 1
    except Exception:
        symbols, universe_size, universe_ready = [], 0, False

    # Analiz zamanlayıcısı — TEK kanonik durum (tercih ≠ gerçek durum)
    sched = scheduler_status(
        controller_status,
        automation_state if automation_state in ("RUNNING", "STOPPED")
        else None)
    controller_running = sched["running"]
    last_cycle = sched["last_run"]
    cycle_err = sched["last_error"]
    scan_s = p["scan_interval_minutes"] * 60
    fresh = False
    if last_cycle:
        try:
            age = time.time() - datetime.fromisoformat(
                last_cycle).timestamp()
            fresh = age < max(3 * scan_s, 900)
        except ValueError:
            fresh = False
    analysis_ready = (sched["enabled"] and controller_running and
                      fresh and not cycle_err and
                      sched["next_run"] is not None)
    analysis_degraded = controller_running and not analysis_ready

    # Karar motoru (scoring/rules tabanlı — dürüst ad: DECISION
    # ENGINE; ML/AI modeli DEĞİLDİR)
    decisions = recent_decisions(5)
    last_decision = decisions[-1].get("ts") if decisions else None
    try:
        import decision_engine  # noqa: F401
        decision_ready = True
    except Exception:
        decision_ready = False

    # Risk engine: adaptive_risk + geçerli profil
    try:
        import adaptive_risk  # noqa: F401
        from services import risk_profiles as rp
        profile = rp.current_profile()
        risk_ready = True
    except Exception:
        profile, risk_ready = None, False

    paper_ready = (automation_state == "RUNNING" and
                   controller_running and not emergency_active)

    stages = {
        "market_data": ("READY" if analysis_ready else
                        "DEGRADED" if analysis_degraded else
                        "NOT_READY"),
        "universe": "READY" if universe_ready else "NOT_READY",
        "analysis": ("READY" if analysis_ready else
                     "DEGRADED" if analysis_degraded else
                     "NOT_READY"),
        "features": "READY" if analysis_ready else "NOT_READY",
        "decision_engine": ("READY" if decision_ready else
                            "NOT_READY"),
        "risk_engine": "READY" if risk_ready else "BLOCKED",
        "paper_controller": ("RUNNING" if paper_ready else
                             "STOPPED"),
    }

    # FALSE GREEN yasağı — GREEN yalnız: scheduler enabled+running,
    # son analiz taze, next_run mevcut, decision+risk READY,
    # controller RUNNING, evren hazır. Blocker'lar dürüstçe listelenir.
    blockers: list[str] = []
    if emergency_active:
        blockers.append("EMERGENCY_STOP_ACTIVE")
    if not sched["enabled"]:
        blockers.append("SCHEDULER_DISABLED")
    elif sched["state"] == "STARTUP_FAILED":
        blockers.append("SCHEDULER_STARTUP_FAILED")
    elif not analysis_ready:
        blockers.append("ANALYSIS_STALE" if last_cycle
                        else "ANALYSIS_NOT_RUN_YET")
    if not decision_ready:
        blockers.append("DECISION_ENGINE_NOT_READY")
    if not risk_ready:
        blockers.append("RISK_ENGINE_NOT_READY")
    if not paper_ready:
        blockers.append("PAPER_CONTROLLER_STOPPED")
    if not universe_ready:
        blockers.append("UNIVERSE_EMPTY")
    # Evren yenilemesi hiç koşmadıysa ya da başarısızsa GREEN yasak —
    # NOT_RUN_YET ile GREEN çelişkisi (Windows saha bulgusu) imkânsız.
    uni_reason = universe_reason_code(universe_size)
    if uni_reason == "NOT_RUN_YET":
        blockers.append("UNIVERSE_NOT_REFRESHED_YET")
    elif uni_reason == "UNIVERSE_REFRESH_FAILED":
        blockers.append("UNIVERSE_REFRESH_FAILED")

    if emergency_active:
        overall = "RED"
    elif not blockers:
        overall = "GREEN"
    elif sched["state"] == "STARTUP_FAILED":
        # tercih RUNNING ama servis başlamadı → görünür arıza
        overall = "YELLOW"
    elif not sched["enabled"]:
        # scheduler kapalı/çalışmıyor → GREEN imkânsız, RED/BLOCKED
        overall = "RED"
    elif decision_ready and risk_ready:
        overall = "YELLOW"
    else:
        overall = "RED"

    return {
        "stages": stages,
        "overall_pipeline": overall,
        "blockers": blockers,
        "universe_size": universe_size,
        "universe_symbols": symbols,
        "universe_reason_code": uni_reason,
        "universe_refresh_result": universe_refresh_result(),
        "scan_interval_minutes": sched["interval_minutes"],
        "analysis_scheduler": sched["state"],
        "analysis_scheduler_detail": sched,
        "selected_risk_profile": (profile["label"] if profile
                                  else "UNKNOWN"),
        "last_complete_analysis": last_cycle,
        "last_decision": last_decision,
        "started": dict(_started),
    }
