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

    # Analiz/karar hattı = controller döngüsü
    controller_running = bool(controller_status.get("running"))
    last_cycle = controller_status.get("last_cycle_time")
    cycle_err = controller_status.get("last_cycle_error")
    scan_s = p["scan_interval_minutes"] * 60
    fresh = False
    if last_cycle:
        try:
            age = time.time() - datetime.fromisoformat(
                last_cycle).timestamp()
            fresh = age < max(3 * scan_s, 900)
        except ValueError:
            fresh = False
    analysis_ready = controller_running and fresh and not cycle_err
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

    # Genel durum — yalnız controller thread'i yetmez:
    # analiz tazeliği + decision + risk hazır olmalı.
    if emergency_active:
        overall = "RED"
    elif (analysis_ready and decision_ready and risk_ready and
          paper_ready and universe_ready):
        overall = "GREEN"
    elif decision_ready and risk_ready and (
            controller_running or automation_state != "RUNNING"):
        overall = "YELLOW"
    else:
        overall = "RED"

    return {
        "stages": stages,
        "overall_pipeline": overall,
        "universe_size": universe_size,
        "universe_symbols": symbols,
        "scan_interval_minutes": p["scan_interval_minutes"],
        "analysis_scheduler": ("RUNNING" if controller_running
                               else "STOPPED"),
        "selected_risk_profile": (profile["label"] if profile
                                  else "UNKNOWN"),
        "last_complete_analysis": last_cycle,
        "last_decision": last_decision,
        "started": dict(_started),
    }
