"""Kalıcı yerel çalışma tercihleri (git dışı, flock'lu).

Kanonik Paper otomasyon/sembol durumları ZATEN
``alpha20_v1/operation_control_state.json`` içindedir (şema sürümlü,
OperationControlStateStore) ve bu modül ona DOKUNMAZ — o şemayı
genişletmek tüm worker'larda STATE_STORE_CORRUPT riski doğurur.

Bu modül aynı git-dışı yerel state sisteminin kardeş dosyasında
YALNIZ yeni kullanıcı tercihlerini saklar:

- selected_risk_profile  (KORUMA | DENGELI | AGRESIF)
- scan_interval_minutes  (varsayılan 5)
- analysis_scheduler     (RUNNING | STOPPED — kullanıcı tercihi)
- universe_max           (en fazla 20)

Kurallar: restart/git pull/SETUP tekrarında korunur, secret içermez,
repoya commit edilmez (.gitignore). Bozuk dosya fail-closed
varsayılanlara DÖNMEZ; hata loglanır ve mevcut değer korunur biçiminde
son bilinen iyi kopya (.bak) denenir.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("runtime_preferences")

ROOT = Path(__file__).resolve().parent.parent
PREFS_PATH = ROOT / "alpha20_v1" / "runtime_preferences.json"

_LOCK = threading.Lock()

VALID_PROFILES = ("KORUMA", "DENGELI", "AGRESIF")
# Türkçe görünüm adları (UI); kod değeri ASCII anahtardır.
PROFILE_LABELS = {"KORUMA": "KORUMA", "DENGELI": "DENGELİ",
                  "AGRESIF": "AGRESİF"}
_ALIASES = {"DENGELİ": "DENGELI", "AGRESİF": "AGRESIF"}

DEFAULTS: dict[str, Any] = {
    "selected_risk_profile": "DENGELI",
    "scan_interval_minutes": 5,
    "analysis_scheduler": "RUNNING",
    "universe_max": 20,
}

MIN_SCAN_MINUTES = 1
MAX_SCAN_MINUTES = 60
HARD_UNIVERSE_MAX = 20


def normalize_profile(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = _ALIASES.get(value.strip().upper(), value.strip().upper())
    return v if v in VALID_PROFILES else None


def _read_raw() -> dict[str, Any]:
    if not PREFS_PATH.exists():
        return {}
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("runtime_preferences okunamadı (%s); "
                  "varsayılanlar KULLANILMAZ, .bak denenir.", exc)
        bak = PREFS_PATH.with_suffix(".json.bak")
        try:
            data = json.loads(bak.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULTS)
    prof = normalize_profile(data.get("selected_risk_profile"))
    if prof:
        out["selected_risk_profile"] = prof
    try:
        scan = int(data.get("scan_interval_minutes",
                            DEFAULTS["scan_interval_minutes"]))
        out["scan_interval_minutes"] = max(
            MIN_SCAN_MINUTES, min(MAX_SCAN_MINUTES, scan))
    except (TypeError, ValueError):
        pass
    sched = data.get("analysis_scheduler")
    if sched in ("RUNNING", "STOPPED"):
        out["analysis_scheduler"] = sched
    try:
        uni = int(data.get("universe_max", DEFAULTS["universe_max"]))
        out["universe_max"] = max(3, min(HARD_UNIVERSE_MAX, uni))
    except (TypeError, ValueError):
        pass
    return out


def get_all() -> dict[str, Any]:
    with _LOCK:
        return _validate(_read_raw())


def get(key: str) -> Any:
    return get_all()[key]


def set_prefs(**kwargs: Any) -> dict[str, Any]:
    """Bir veya birden çok tercihi atomik yaz (tmp+replace)."""
    with _LOCK:
        current = _read_raw()
        current.update(kwargs)
        validated = _validate(current)
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Son bilinen iyi kopya
        if PREFS_PATH.exists():
            try:
                PREFS_PATH.with_suffix(".json.bak").write_text(
                    PREFS_PATH.read_text(encoding="utf-8"),
                    encoding="utf-8")
            except OSError:
                pass
        tmp = PREFS_PATH.with_name(
            f".{PREFS_PATH.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(validated, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(PREFS_PATH)
        return validated
