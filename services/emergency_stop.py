"""
emergency_stop.py — PAPER acil durdurma (kill-switch) durumunun TEK
salt-okunur görünümü + güvenli temizleme yardımcıları.

YENİ durum sistemi DEĞİLDİR. İki MEVCUT kanonik kaynağı okur:

1. alpha20_v1/config.json → adaptive_system.kill_switch
   (dashboard / Operation Center'ın gösterdiği bayrak; /adaptive/kill-switch
   ve Operation Center kill-switch rotaları yazar)
2. alpha20_v1/safety_state.json → kill_switch (+ neden alanları)
   (safety_guard; decision_engine yeni işlem açılmasını buradan bloklar)

Kilit AKTİF sayılır iki kaynaktan HERHANGİ Bİ­Rİ true ise — böylece
dashboard ile karar motoru asla sessizce ayrışmaz.

Neden modeli safety_state.json içindeki kill_switch_reason_* alanlarından
gelir (safety_guard.activate_kill_switch yazar). Neden kaydı olmayan eski
kilit UNKNOWN_LEGACY_STATE olarak sınıflandırılır — sessizce güvenli olay
gibi gösterilmez.

Temizleme YAZMA işlemleri bu modülde YAPILMAZ: app.py rotası mevcut
kanonik yazarları (safety_guard.deactivate_kill_switch + adaptive config
kaydetme) kullanır. Bu modül yalnız sınıflandırma, sağlık kontrolü ve
temizlik öncesi YEDEK dosyasını üretir. Secret içermez, ağ çağrısı yapmaz.
LIVE ORDERS her durumda DISABLED — bu modül canlı emir yolu açmaz.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ALPHA_DIR = ROOT / "alpha20_v1"
CONFIG_PATH = ALPHA_DIR / "config.json"
BACKUP_DIR = ALPHA_DIR / "emergency_stop_backups"
OPERATION_STATE_PATH = ALPHA_DIR / "operation_control_state.json"

# Risk kaynaklı GERÇEK durdurmalar — panelden tek tıkla TEMİZLENEMEZ.
RISK_REASON_CODES = frozenset({"RISK_LIMIT", "CONSECUTIVE_LOSSES"})

# Bilinen neden kodları (kapalı küme; dışındakiler UNKNOWN_LEGACY_STATE).
KNOWN_REASON_CODES = frozenset({
    "MANUAL_STOP", "RISK_LIMIT", "CONSECUTIVE_LOSSES", "DATA_OUTAGE",
    "STARTUP_FAILURE", "STALE_TEST_STATE", "UNKNOWN_LEGACY_STATE",
})


def _sg():
    """safety_guard modülü — alpha20_v1 sys.path önceliğiyle."""
    alpha = str(ALPHA_DIR)
    if alpha not in sys.path:
        sys.path.insert(0, alpha)
    import safety_guard  # noqa: PLC0415
    return safety_guard


def _adaptive_cfg() -> dict[str, Any]:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        adaptive = cfg.get("adaptive_system")
        return adaptive if isinstance(adaptive, dict) else {}
    except Exception:
        return {}


def _execution_mode() -> str:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        mode = str(cfg.get("execution_mode") or "PAPER").upper()
        return mode or "PAPER"
    except Exception:
        return "PAPER"


def automation_mode() -> str:
    """Operation Center otomasyon durumundan ADVISOR/AUTOMATIC etiketi.

    RUNNING → AUTOMATIC; diğer her durum (STOPPED/PAUSED/BLOCKED/…)
    panelde DANIŞMAN (ADVISOR) olarak görünür. Bu BAĞIMSIZ bir operatör
    tercihidir; acil durdurma temizlenince sessizce değiştirilmez."""
    try:
        with OPERATION_STATE_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
        return ("AUTOMATIC"
                if state.get("automation_state") == "RUNNING"
                else "ADVISOR")
    except Exception:
        return "ADVISOR"


def status() -> dict[str, Any]:
    """Acil durdurma durumunun secret'sız, salt-okunur özeti."""
    sg = _sg()
    safety = sg.get_safety_state()
    cfg_ks = bool(_adaptive_cfg().get("kill_switch", False))
    sg_ks = bool(safety.get("kill_switch", False))
    active = cfg_ks or sg_ks

    reason_code = str(safety.get("kill_switch_reason_code") or "").strip()
    reason_text = str(safety.get("kill_switch_reason_text") or "").strip()
    triggered_at = safety.get("kill_switch_triggered_at")
    triggered_by = str(safety.get("kill_switch_triggered_by") or "").strip()
    if active and reason_code not in KNOWN_REASON_CODES:
        # Neden kaydı olmayan eski/artık kilit — dürüst sınıflandırma.
        reason_code = "UNKNOWN_LEGACY_STATE"
        if not reason_text:
            reason_text = ("Neden kaydı bulunmayan eski/artık kilit "
                           "(muhtemelen bayat test/sapma durumu).")
        triggered_by = triggered_by or "unknown"
    if not active:
        reason_code, reason_text = "", ""

    mode = _execution_mode()
    risk_locked = reason_code in RISK_REASON_CODES
    can_clear = bool(active and not risk_locked and mode != "LIVE")
    requirements = [
        "PAPER modu (LIVE değil)",
        "Canlı emirler DISABLED (her zaman)",
        "Yerel Windows runtime + oturum + CSRF",
        "Açık kullanıcı onayı",
        "Temizlik öncesi otomatik durum yedeği",
    ]
    if risk_locked:
        requirements.insert(0, "RİSK kaynaklı durdurma — panelden tek "
                               "tıkla temizlenemez; nedeni giderin.")
    return {
        "active": active,
        "sources": {"adaptive_config": cfg_ks, "safety_state": sg_ks},
        "reason_code": reason_code,
        "reason_text": reason_text,
        "triggered_at": triggered_at,
        "triggered_by": triggered_by,
        "environment": mode,
        "automation_mode": automation_mode(),
        "can_clear": can_clear,
        "risk_protected": risk_locked,
        "clear_requirements": requirements,
        "live_orders": "DISABLED",
    }


def health_check() -> tuple[bool, str]:
    """Temizlik öncesi sağlık kontrolü — ağ çağrısı YAPMAZ.

    Controller durum kaydı okunabilmeli ve yürütme modu LIVE olmamalı.
    Public market data sağlığı Windows'ta controller çevrimleriyle
    görünür; tek sembollük geçici SSL hatası burada blokaj DEĞİLDİR."""
    if _execution_mode() == "LIVE":
        return False, "LIVE modda panelden kilit kaldırılamaz (fail-closed)."
    try:
        alpha = str(ALPHA_DIR)
        if alpha not in sys.path:
            sys.path.insert(0, alpha)
        import auto_controller  # noqa: PLC0415
        auto_controller.get_status()
    except Exception:
        return False, "Controller durum kaydı okunamadı."
    try:
        _sg().get_safety_state()
    except Exception:
        return False, "Güvenlik durumu okunamadı."
    return True, "OK"


def write_backup(actor: str) -> str:
    """Temizlik ÖNCESİ tek dosyalık durum yedeği; dosya adını döndürür.

    Yedek secret içermez: yalnız adaptive_system bayrakları ve
    safety_state alanları."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"emergency_stop_{ts}.json"
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "actor": str(actor or "operator"),
        "adaptive_system": _adaptive_cfg(),
        "safety_state": _sg().get_safety_state(),
    }
    path = BACKUP_DIR / name
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return name
