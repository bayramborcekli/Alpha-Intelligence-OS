"""Mission 1600 — Intelligence Automation çekirdeği (Agent 02).

Intelligence Engine'in kontrollü, tekil ve deterministik biçimde
otomatik çalıştırılmasını sağlar. Başarılı koşu sonucu YALNIZ mevcut
resmî yüzey olan ``intelligence_timeline.append_snapshot`` ile
kaydedilir; başarısız koşuda snapshot yazılmaz.

Sözleşmeler (baseline — değiştirilemez):
- Timeline append-only kalır; bu modül timeline'ı değiştirmez.
- Exchange/Ledger/Audit erişimi yoktur (statik testli).
- Otomatik retry yoktur; INTERRUPTED koşular append üretmez.
- Aynı otomasyon aynı anda iki kez çalışamaz (süreçler-arası flock).
- Bilinmeyen değerler null kalır; Decimal değerler dokunulmadan
  ``append_snapshot`` doğrulamasına aktarılır (float yasağı orada).

Bu modül REST endpoint, UI veya export içermez (Agent 04-06 kapsamı).
"""

from __future__ import annotations

try:
    import fcntl  # POSIX (Linux/Replit) — davranış değişmez
except ImportError:  # Windows: msvcrt tabanlı uyumluluk katmanı
    import portable_flock as fcntl  # type: ignore
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import intelligence_timeline

# ── Durumlar ─────────────────────────────────────────────────────────

STATE_DISABLED = "disabled"
STATE_SCHEDULED = "scheduled"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"

VALID_STATES = (STATE_DISABLED, STATE_SCHEDULED, STATE_RUNNING,
                STATE_SUCCEEDED, STATE_FAILED)

# Geçerli geçişler (durum makinesi — Agent 01 kararı)
_TRANSITIONS = {
    STATE_DISABLED: {STATE_SCHEDULED},
    STATE_SCHEDULED: {STATE_RUNNING, STATE_DISABLED},
    STATE_RUNNING: {STATE_SUCCEEDED, STATE_FAILED},
    STATE_SUCCEEDED: {STATE_RUNNING, STATE_DISABLED},
    STATE_FAILED: {STATE_RUNNING, STATE_DISABLED},
}

# Hata kodları (sterile — asla exception metni saklanmaz)
ERROR_INTERRUPTED = "INTERRUPTED"
ERROR_EXECUTION_FAILED = "EXECUTION_FAILED"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_INVALID_RESULT = "INVALID_RESULT"
ERROR_APPEND_FAILED = "APPEND_FAILED"
SKIP_DUPLICATE = "DUPLICATE_RUN"
SKIP_DISABLED = "DISABLED"
SKIP_NOT_DUE = "NOT_DUE"

# Yalnız bu statüler snapshot olarak kaydedilir
_RECORDABLE_STATUSES = ("OK", "PARTIAL")

DEFAULT_STATE_PATH = Path("automation_state.json")
_STATE_ENV = "ALPHA_AUTOMATION_STATE_PATH"

# ── Yapılandırma (intelligence_settings deseni: geçersizde güvenli
#    varsayılan; ham ortam değeri asla dışarı verilmez) ───────────────

DEFAULT_INTERVAL_MINUTES = 60
MIN_INTERVAL_MINUTES = 5
DEFAULT_TIMEOUT_SECONDS = 120
MIN_TIMEOUT_SECONDS = 10


def load_config() -> dict:
    """Ortamdan doğrulanmış otomasyon yapılandırması üretir."""
    enabled = os.environ.get("ALPHA_AUTOMATION_ENABLED", "").strip().lower() == "true"

    def _int(name: str, default: int, minimum: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value >= minimum else minimum

    return {
        "enabled": enabled,
        "interval_minutes": _int("ALPHA_AUTOMATION_INTERVAL_MINUTES",
                                 DEFAULT_INTERVAL_MINUTES,
                                 MIN_INTERVAL_MINUTES),
        "timeout_seconds": _int("ALPHA_AUTOMATION_TIMEOUT_SECONDS",
                                DEFAULT_TIMEOUT_SECONDS,
                                MIN_TIMEOUT_SECONDS),
    }


# ── Durum saklama ────────────────────────────────────────────────────

def _state_path(path: str | os.PathLike | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get(_STATE_ENV)
    return Path(env) if env else DEFAULT_STATE_PATH


def _lock_path(path: str | os.PathLike | None = None) -> Path:
    p = _state_path(path)
    return p.with_name(p.name + ".lock")


_EMPTY_STATE = {
    "state": STATE_DISABLED,
    "run_id": None,
    "last_run_started_at": None,
    "last_run_finished_at": None,
    "last_run_status": None,
    "last_error_code": None,
    "last_snapshot_recorded": None,
    "last_duration_seconds": None,
}


def load_state(path: str | os.PathLike | None = None) -> dict:
    """Durum dosyasını okur; yoksa/bozuksa güvenli boş durum döner."""
    p = _state_path(path)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return dict(_EMPTY_STATE)
    if not isinstance(data, dict) or data.get("state") not in VALID_STATES:
        return dict(_EMPTY_STATE)
    merged = dict(_EMPTY_STATE)
    merged.update({k: data.get(k) for k in _EMPTY_STATE})
    return merged


def _save_state(state: dict, path: str | os.PathLike | None = None) -> None:
    p = _state_path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({k: state.get(k) for k in _EMPTY_STATE}, fh,
                  sort_keys=True, ensure_ascii=False)
    os.replace(tmp, p)


def transition(state: dict, new_state: str) -> dict:
    """Durum makinesi geçişi; geçersiz geçiş ValueError üretir."""
    current = state.get("state")
    if new_state not in VALID_STATES:
        raise ValueError("INVALID_STATE")
    if new_state == current:
        raise ValueError("INVALID_TRANSITION")
    if new_state not in _TRANSITIONS.get(current, set()):
        raise ValueError("INVALID_TRANSITION")
    out = dict(state)
    out["state"] = new_state
    return out


# ── Zamanlama kararı ─────────────────────────────────────────────────

def should_run(state: dict, config: dict, now_epoch: float,
               last_finished_epoch: float | None) -> tuple[bool, str | None]:
    """Otomatik koşunun vadesinin gelip gelmediğine karar verir."""
    if not config.get("enabled"):
        return False, SKIP_DISABLED
    if state.get("state") == STATE_RUNNING:
        return False, SKIP_DUPLICATE
    if last_finished_epoch is None:
        return True, None
    due = last_finished_epoch + config["interval_minutes"] * 60
    if now_epoch >= due:
        return True, None
    return False, SKIP_NOT_DUE


# ── Kurtarma (restart davranışı) ─────────────────────────────────────

def recover_interrupted(path: str | os.PathLike | None = None) -> dict:
    """Çökme sonrası 'running' kalmış durumu INTERRUPTED-failed yapar.

    run_once ile aynı flock kilidi altında çalışır: kilit alınamazsa
    koşu HÂLÂ AKTİFTİR ve dokunulmaz (yalnız bayat durum işaretlenir).
    Otomatik retry veya snapshot append ÜRETMEZ; bir sonraki interval
    beklenir (Agent 01 kararı — mükerrer append riski sıfırlanır).
    """
    fh = open(_lock_path(path), "a", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return load_state(path)  # aktif koşu var — dokunma
        state = load_state(path)
        if state.get("state") != STATE_RUNNING:
            return state
        state = transition(state, STATE_FAILED)
        state["last_run_status"] = STATE_FAILED
        state["last_error_code"] = ERROR_INTERRUPTED
        state["last_snapshot_recorded"] = False
        _save_state(state, path)
        return state
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


# ── Koşu (runner + coordinator) ──────────────────────────────────────

def run_once(summary_provider: Callable[[], Any],
             *,
             config: dict | None = None,
             state_path: str | os.PathLike | None = None,
             history_path: str | os.PathLike | None = None,
             now_iso: str | None = None,
             clock: Callable[[], float] = time.monotonic,
             force: bool = False) -> dict:
    """Tek kontrollü otomasyon koşusu yürütür.

    - Süreçler-arası flock(LOCK_EX|LOCK_NB) ile tekil koşu garantisi;
      kilit alınamazsa koşu ATLANIR (DUPLICATE_RUN).
    - Başarıda (status OK/PARTIAL) yalnız ``append_snapshot`` çağrılır.
    - Hata/timeout/geçersiz sonuçta snapshot YAZILMAZ; retry YOKTUR.
    - Sterile: exception metni asla saklanmaz, yalnız hata kodu.
    """
    cfg = config if config is not None else load_config()
    if not force and not cfg.get("enabled"):
        return {"ran": False, "skip_reason": SKIP_DISABLED,
                "appended": False, "error_code": None}

    lock_file = _lock_path(state_path)
    fh = open(lock_file, "a", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"ran": False, "skip_reason": SKIP_DUPLICATE,
                    "appended": False, "error_code": None}

        state = load_state(state_path)
        if state["state"] == STATE_RUNNING:
            # Aynı süreçte kilidi tutan başka koşu olamaz; bu artık
            # kesintiye uğramış eski bir durumdur.
            state = transition(state, STATE_FAILED)
            state["last_error_code"] = ERROR_INTERRUPTED
            state["last_snapshot_recorded"] = False
        if state["state"] == STATE_DISABLED:
            state = transition(state, STATE_SCHEDULED)

        started_iso = now_iso or _utc_now_iso()
        run_id = started_iso
        state = transition(state, STATE_RUNNING)
        state.update({"run_id": run_id,
                      "last_run_started_at": started_iso,
                      "last_run_finished_at": None,
                      "last_run_status": STATE_RUNNING,
                      "last_error_code": None,
                      "last_snapshot_recorded": None,
                      "last_duration_seconds": None})
        _save_state(state, state_path)

        t0 = clock()
        error_code = None
        result = None
        try:
            result = summary_provider()
        except Exception:
            error_code = ERROR_EXECUTION_FAILED
        duration = clock() - t0

        appended = False
        if error_code is None:
            if duration > cfg["timeout_seconds"]:
                error_code = ERROR_TIMEOUT
            elif not isinstance(result, dict) or \
                    result.get("status") not in _RECORDABLE_STATUSES:
                error_code = ERROR_INVALID_RESULT
            else:
                try:
                    intelligence_timeline.append_snapshot(
                        result, history_path)
                    appended = True
                except Exception:
                    # Append denemesi sonrası ASLA retry yok
                    # (mükerrer kayıt riski — Agent 01 kararı).
                    error_code = ERROR_APPEND_FAILED

        final = STATE_SUCCEEDED if error_code is None else STATE_FAILED
        state = transition(state, final)
        state.update({"last_run_finished_at": _utc_now_iso()
                      if now_iso is None else now_iso,
                      "last_run_status": final,
                      "last_error_code": error_code,
                      "last_snapshot_recorded": appended,
                      "last_duration_seconds": round(duration, 3)})
        _save_state(state, state_path)
        return {"ran": True, "skip_reason": None, "appended": appended,
                "error_code": error_code, "run_id": run_id,
                "final_state": final}
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Scheduler döngüsü (worker-içi daemon thread; API bağlanışı
#    Agent 04 kapsamındadır) ─────────────────────────────────────────

_POLL_SECONDS = 30.0


def scheduler_tick(summary_provider: Callable[[], Any],
                   *,
                   config: dict | None = None,
                   state_path: str | os.PathLike | None = None,
                   history_path: str | os.PathLike | None = None,
                   now_epoch: float | None = None) -> dict:
    """Tek zamanlayıcı vuruşu: vade dolduysa koşuyu başlatır."""
    cfg = config if config is not None else load_config()
    state = load_state(state_path)
    last_finish = _epoch_of(state.get("last_run_finished_at"))
    now = time.time() if now_epoch is None else now_epoch
    ok, reason = should_run(state, cfg, now, last_finish)
    if not ok:
        return {"ran": False, "skip_reason": reason,
                "appended": False, "error_code": None}
    return run_once(summary_provider, config=cfg, state_path=state_path,
                    history_path=history_path)


def _epoch_of(iso: str | None) -> float | None:
    if not iso:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def start_loop(summary_provider: Callable[[], Any],
               *,
               state_path: str | os.PathLike | None = None,
               history_path: str | os.PathLike | None = None,
               poll_seconds: float = _POLL_SECONDS,
               stop_event: threading.Event | None = None) -> threading.Thread:
    """Daemon zamanlayıcı thread'i başlatır (mevcut post_fork deseni)."""
    stop = stop_event or threading.Event()

    def _loop() -> None:
        recover_interrupted(state_path)
        while not stop.is_set():
            try:
                scheduler_tick(summary_provider, state_path=state_path,
                               history_path=history_path)
            except Exception:
                pass  # sterile: döngü asla exception ile ölmez
            stop.wait(poll_seconds)

    thread = threading.Thread(target=_loop, name="intelligence_automation",
                              daemon=True)
    thread._alpha_stop_event = stop  # test erişimi için
    thread.start()
    return thread
