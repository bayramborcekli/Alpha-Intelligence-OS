"""
auto_controller.py — Tüm modülleri yöneten otomatik çalışma döngüsü.
Aynı anda yalnızca bir instance çalışır; her döngü adımı hata yönetimi içerir.
"""
from __future__ import annotations

try:
    import fcntl  # POSIX — davranış değişmez
except ImportError:  # Windows: proje kökündeki uyumluluk katmanı
    import portable_flock as fcntl  # type: ignore
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("auto_controller")

ROOT        = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH  = ROOT / "state.json"
LOCK_FILE   = ROOT / ".auto_controller.lock"

# Döngü kilidi — iki tane controller başlamasını engelle
_LOOP_RUNNING    = threading.Event()
_CONTROLLER_LOCK = threading.Lock()

# Son durum (panel API tarafından okunur)
_last_status: dict[str, Any] = {
    "running":         False,
    "mode":            "MONITOR",
    "last_cycle_time": None,
    "last_cycle_error": None,
    "next_cycle_time": None,
    "last_decision_time": None,
    "last_learning_time": None,
    "model_version":   1,
    "cycle_count":     0,
    "kill_switch":     False,
    "safe_mode":       False,
    "safe_mode_reason": "",
}
_status_lock = threading.Lock()


def get_status() -> dict[str, Any]:
    with _status_lock:
        st = dict(_last_status)
    # Salt-okunur görünürlük: Windows PAPER runtime override aktif mi?
    # (config.json'a yazılmayan, yalnız bellekte yaşayan bayraklar.)
    st["runtime_override"] = bool(RUNTIME_ADAPTIVE_OVERRIDE)
    if RUNTIME_ADAPTIVE_OVERRIDE:
        st["runtime_override_flags"] = dict(RUNTIME_ADAPTIVE_OVERRIDE)
    return st


def _update_status(**kwargs: Any) -> None:
    with _status_lock:
        _last_status.update(kwargs)
    # GÖREV 116: Scheduler sahibi worker durumunu PAYLAŞIMLI kanonik
    # snapshot dosyasına da yazar — diğer gunicorn worker'ları process-
    # local bellek yerine bu dosyadan okur (sahte STARTUP_FAILED biter).
    # Yalnız döngü sahibi yazar: _LOOP_RUNNING seti veya 'running'
    # anahtarı (start/stop geçişi) varsa. Diğer worker'ların yerel
    # safe_mode vb. güncellemeleri paylaşımlı durumu KİRLETEMEZ.
    if _LOOP_RUNNING.is_set() or "running" in kwargs:
        _persist_shared_status()


# ── GÖREV 116: paylaşımlı kanonik scheduler snapshot (git dışı) ──────────
SHARED_STATUS_PATH = ROOT / "controller_status_runtime.json"


def _persist_shared_status() -> None:
    """Yerel durumu atomik olarak paylaşımlı dosyaya yaz (tmp+replace)."""
    try:
        with _status_lock:
            snap = dict(_last_status)
        snap["pid"] = os.getpid()
        snap["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp = SHARED_STATUS_PATH.with_suffix(
            f".{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, SHARED_STATUS_PATH)
    except Exception as exc:  # snapshot yazımı asla döngüyü kırmaz
        log.warning("Paylaşımlı durum snapshot yazılamadı: %s", exc)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def get_shared_status() -> dict[str, Any]:
    """Paylaşımlı kanonik snapshot'ı oku (salt-okunur).

    Dönen sözlükte 'owner_alive' alanı vardır: snapshot'ı yazan sürecin
    hâlâ yaşadığı os.kill(pid, 0) ile doğrulanır. Sahip ölmüşse
    fail-closed: owner_alive=False → çağıran taraf RUNNING kabul EDEMEZ
    (gerçek arıza maskelenmez).
    """
    try:
        with SHARED_STATUS_PATH.open("r", encoding="utf-8") as f:
            snap = json.load(f)
        if not isinstance(snap, dict):
            return {}
    except (OSError, json.JSONDecodeError):
        return {}
    snap["owner_alive"] = _pid_alive(snap.get("pid", -1))
    return snap


# ══════════════════════════════════════════════════════════════════════════════
# Config yardımcıları
# ══════════════════════════════════════════════════════════════════════════════

# Yalnız BELLEKTE tutulan adaptive_system override'ı (Windows local PAPER
# testi). config.json'a ASLA yazılmaz; süreç kapanınca kaybolur.
RUNTIME_ADAPTIVE_OVERRIDE: dict[str, Any] = {}


# Bellek-içi tarama aralığı override'ı (saniye). Kalıcı kaynak:
# services/runtime_preferences.py (scan_interval_minutes, varsayılan 5).
# config.json'a yazılmaz; orchestrator her başlangıçta yeniden uygular.
RUNTIME_SCAN_SECONDS: dict[str, int] = {}


def set_runtime_scan_seconds(seconds: int | None) -> None:
    """Tarama aralığını bellekte override et (None = kaldır)."""
    RUNTIME_SCAN_SECONDS.clear()
    if seconds is not None and seconds > 0:
        RUNTIME_SCAN_SECONDS["value"] = int(seconds)
        log.info("Runtime scan override: %ss (yalnız bellek).", seconds)


def set_runtime_adaptive_override(flags: dict[str, Any]) -> None:
    """Bellek-içi adaptive_system override'ını ayarla (dosyaya yazmaz)."""
    RUNTIME_ADAPTIVE_OVERRIDE.clear()
    RUNTIME_ADAPTIVE_OVERRIDE.update(flags)
    log.info("Runtime adaptive override aktif (yalnız bellek): %s", flags)


def _load_config() -> dict[str, Any] | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    if RUNTIME_ADAPTIVE_OVERRIDE and isinstance(cfg, dict):
        merged = dict(cfg.get("adaptive_system") or {})
        merged.update(RUNTIME_ADAPTIVE_OVERRIDE)
        cfg = dict(cfg)
        cfg["adaptive_system"] = merged
    return cfg


def _load_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_state(state: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_name(".state.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(STATE_PATH)
    except OSError as exc:
        log.error("state.json kaydedilemedi: %s", exc)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _validate_adaptive_config(cfg: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Adaptive section doğrula; geçersiz değerlerde varsayılana dön."""
    DEFAULTS = {
        "enabled":                    False,
        "mode":                       "MONITOR",
        "auto_paper_enabled":         False,
        "regime_min_confidence":      65,
        "final_decision_threshold":   78,
        "base_risk_pct":              0.25,
        "max_risk_pct":               0.50,
        "daily_loss_limit_pct":       1.0,
        "max_drawdown_pct":           5.0,
        "max_consecutive_losses":     3,
        "risk_reduction_after_losses": 2,
        "learning_enabled":           True,
        "learning_interval_hours":    24,
        "minimum_learning_trades":    20,
        "max_daily_weight_change_pct": 5,
        "cooldown_minutes":           60,
        "break_even_enabled":         False,
        "trailing_stop_enabled":      False,
        "partial_take_profit_enabled": False,
        "kill_switch":                False,
    }
    adaptive = cfg.get("adaptive_system", {})
    merged   = dict(DEFAULTS)
    merged.update(adaptive)

    errors: list[str] = []
    if not (0 < merged["base_risk_pct"] <= 0.50):
        merged["base_risk_pct"] = 0.25
        errors.append("base_risk_pct sıfırlandı.")
    if not (0 < merged["max_risk_pct"] <= 0.50):
        merged["max_risk_pct"] = 0.50
        errors.append("max_risk_pct sıfırlandı.")
    if not (0.1 <= merged["daily_loss_limit_pct"] <= 5.0):
        merged["daily_loss_limit_pct"] = 1.0
        errors.append("daily_loss_limit_pct sıfırlandı.")

    ok = len(errors) == 0
    return ok, merged


# ══════════════════════════════════════════════════════════════════════════════
# Tek döngü adımı
# ══════════════════════════════════════════════════════════════════════════════

def _run_single_cycle(
    adaptive_cfg: dict[str, Any],
    symbols: list[str],
) -> None:
    """Bir tarama döngüsü — hata yönetimli."""
    import metrics_store as ms
    import safety_guard  as sg
    import market_regime as mr
    import adaptive_risk as ar
    import decision_engine as de
    import learning_engine as le

    mode          = adaptive_cfg.get("mode", "MONITOR")
    auto_paper    = adaptive_cfg.get("auto_paper_enabled", False)
    cooldown_min  = int(adaptive_cfg.get("cooldown_minutes", 60))
    reward_risk   = float(adaptive_cfg.get("reward_risk_ratio", 2.0)) if "reward_risk_ratio" in adaptive_cfg else 2.0

    trading_state = _load_state()
    if trading_state is None:
        ms.append_system_error(component="auto_controller",
                               error_type="STATE_READ_ERROR",
                               message="state.json okunamadı.", safe_state_activated=True)
        sg.lock_safety("state.json okunamadı.")
        _update_status(safe_mode=True, safe_mode_reason="state.json okunamadı.")
        return

    # 0. Açık pozisyon yönetimi — KANONİK SL/TP kontrolü.
    # alpha20.manage_position tek kaynak: klasik motor (run_cycle) ile aynı
    # fonksiyon. Safety guard'dan ÖNCE çalışır (run_cycle sırasıyla aynı):
    # güvenlik kilidi yeni işlem açmayı durdursa bile açık pozisyon
    # izlenmeye ve SL/TP'de kapanmaya devam etmelidir.
    if trading_state.get("position"):
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import alpha20
        try:
            alpha20.manage_position(trading_state)
            _save_state(trading_state)  # kapanış/muhasebe kalıcı olsun
        except Exception as exc:
            log.warning("Pozisyon yönetimi hatası: %s", exc)
            ms.append_system_error(component="auto_controller",
                                   error_type="POSITION_MANAGE_ERROR",
                                   message=str(exc)[:200])

    # 1. Safety Guard
    safety = sg.check_all(trading_state=trading_state, adaptive_cfg=adaptive_cfg)
    _update_status(kill_switch=safety.kill_switch, safe_mode=not safety.safe,
                   safe_mode_reason="" if safety.safe else safety.reason)
    if not safety.safe:
        ms.update_panel_status({"safety": safety.to_dict()})
        return

    # 2. Genel piyasa rejimi
    market_regime_info: dict[str, Any] = {"regime": "Veri Yetersiz", "confidence": 0, "suitable": False}
    try:
        market_regime_info = mr.detect_market_regime(symbols)
        ms.append_regime(
            symbol="MARKET",
            regime=market_regime_info["regime"],
            confidence=market_regime_info["confidence"],
            direction=market_regime_info.get("direction", "—"),
            volatility=market_regime_info.get("volatility", "—"),
            trend_strength=float(market_regime_info.get("trend_strength", 0)),
            suitable=bool(market_regime_info.get("suitable", False)),
            reason=market_regime_info.get("reason", ""),
        )
    except Exception as exc:
        log.warning("Piyasa rejimi alınamadı: %s", exc)

    ms.update_panel_status({"market_regime": market_regime_info})

    # 3. Her sembol için sinyal + karar
    decisions    = []
    weights      = le.load_weights()
    weight_dict  = {k: v for k, v in weights.items() if not str(k).startswith("_")}

    current_pos  = trading_state.get("position")
    pos_symbol   = current_pos.get("symbol") if isinstance(current_pos, dict) else None

    for symbol in symbols:
        try:
            _process_symbol(
                symbol=symbol,
                trading_state=trading_state,
                adaptive_cfg=adaptive_cfg,
                market_regime_info=market_regime_info,
                mode=mode,
                auto_paper=auto_paper,
                cooldown_min=cooldown_min,
                weight_dict=weight_dict,
                pos_symbol=pos_symbol,
                decisions=decisions,
                reward_risk=reward_risk,
            )
        except Exception as exc:
            log.warning("Sembol işlenirken hata (%s): %s", symbol, exc)
            ms.append_system_error(component="auto_controller",
                                   error_type="SYMBOL_ERROR",
                                   message=f"{symbol}: {str(exc)[:200]}")

    ms.update_panel_status({"last_decisions": decisions[:20],
                            "last_cycle": datetime.now(timezone.utc).isoformat()})
    _update_status(last_decision_time=datetime.now(timezone.utc).isoformat())


def _process_symbol(
    symbol: str,
    trading_state: dict[str, Any],
    adaptive_cfg: dict[str, Any],
    market_regime_info: dict[str, Any],
    mode: str,
    auto_paper: bool,
    cooldown_min: int,
    weight_dict: dict[str, float],
    pos_symbol: str | None,
    decisions: list,
    reward_risk: float,
) -> None:
    import metrics_store   as ms
    import safety_guard    as sg
    import market_regime   as mr
    import adaptive_risk   as ar
    import decision_engine as de
    import learning_engine as le

    config = _load_config() or {}

    # alpha20'nin fonksiyonlarını kullan
    import sys
    sys.path.insert(0, str(ROOT))
    import alpha20

    try:
        df_fast  = alpha20.fetch_klines(symbol, config.get("interval", "15m"))
        df_trend = alpha20.fetch_klines(symbol, config.get("trend_interval", "1h"))
        df_fast  = alpha20.add_indicators(df_fast)
        df_trend = alpha20.add_indicators(df_trend)
    except Exception as exc:
        raise RuntimeError(f"Kline alınamadı: {exc}") from exc

    side, strategy_score, details = alpha20.score_setup(df_fast, df_trend)
    price = float(details["price"])
    atr   = float(details["atr"])

    # Coin-bazlı rejim
    coin_regime_info = mr.detect_regime(symbol)
    regime_score_val = mr.regime_score(coin_regime_info)

    # Veri kalitesi
    dq_score = de.calculate_data_quality(
        df_15m_len=len(df_fast), df_1h_len=len(df_trend),
        timestamp_ok=True, price=price,
        prev_price=price,  # basit: prev bilinmiyor
    )

    # Hacim (ticker'dan al)
    vol_24h = float(details.get("volume_ratio", 1) * 1e9)  # tahmin

    # Paper geçmiş puanı
    trades = trading_state.get("trades", [])
    ph_score = le.get_paper_history_score(symbol, trades if isinstance(trades, list) else [])

    # Karar skoru
    final_score, category, components, reason = de.score_decision(
        strategy_score=float(strategy_score),
        regime_score=regime_score_val,
        regime_confidence=float(coin_regime_info.get("confidence", 0)),
        coin_score=float(strategy_score),   # coin score ≈ strategy score
        volume_24h_usdt=vol_24h,
        atr_pct=float(coin_regime_info.get("atr_pct", 2.0)),
        regime=coin_regime_info.get("regime", "Belirsiz"),
        paper_hist_score=ph_score,
        data_quality_score=dq_score,
        weights_override=weight_dict,
    )

    # Risk izni
    risk_res = ar.calculate_risk(
        trading_state=trading_state,
        adaptive_cfg=adaptive_cfg,
        regime_info=coin_regime_info,
        final_decision_score=final_score,
        data_quality_score=dq_score,
    )

    # Cooldown kontrolü
    cooldown_ok = _check_cooldown(symbol, trading_state, cooldown_min)

    # Koşullar
    max_pos     = int(config.get("max_open_positions", 1))
    open_count  = 1 if trading_state.get("position") else 0
    approved, cond_reason = de.check_conditions(
        final_score=final_score,
        regime_confidence=float(coin_regime_info.get("confidence", 0)),
        data_quality_score=dq_score,
        liquidity_score=70.0,   # varsayılan
        risk_allowed=risk_res.allowed,
        daily_loss_ok=not sg.get_safety_state().get("daily_loss_block"),
        max_positions_ok=open_count < max_pos,
        symbol_no_position=pos_symbol != symbol,
        cooldown_ok=cooldown_ok,
        kill_switch_off=not sg.get_safety_state().get("kill_switch"),
        adaptive_cfg=adaptive_cfg,
    )

    # Karar tipi
    if not approved:
        decision_type = "REJECT"
        decision_reason = cond_reason
    elif final_score >= 75:
        decision_type = "OPEN"
        decision_reason = reason
    elif final_score >= 50:
        decision_type = "WATCH"
        decision_reason = reason
    else:
        decision_type = "REJECT"
        decision_reason = reason

    # Log — Decision Trace: her karar kaydında profil, veri durumu ve
    # nihai karar nedeni zorunlu alanlarla saklanır ("neden işlem
    # açılmadı?" sorusunun kanıtı).
    trace_fields: dict[str, Any] = {
        "correlation_id": f"dt-{symbol}-{int(time.time() * 1000)}",
        "data_status": ("DATA_FRESH" if dq_score >= 70 else
                        "DATA_DEGRADED" if dq_score >= 40 else
                        "DATA_STALE"),
        "signal_direction": side or "NONE",
        "decision_score": round(final_score, 1),
        "required_threshold": float(
            adaptive_cfg.get("final_decision_threshold",
                             de.DEFAULT_AUTO_THRESHOLD)),
        "calculated_position_size": 0.0,  # OPEN'da güncellenir
        "risk_result": risk_res.to_dict() if hasattr(
            risk_res, "to_dict") else {
            "allowed": risk_res.allowed,
            "risk_pct": risk_res.risk_pct},
        "cooldown_status": "OK" if cooldown_ok else "COOLDOWN",
        "final_decision": ("OPEN" if decision_type == "OPEN"
                           else "NO_TRADE"),
        "rejection_reason": (decision_reason
                             if decision_type != "OPEN" else ""),
    }
    try:
        from services import risk_profiles as rp
        trace_fields.update(rp.decision_fields())
    except Exception:
        pass
    de.log_decision(
        symbol=symbol, price=price, side=side,
        final_score=final_score, category=category,
        components=components, regime=coin_regime_info.get("regime", ""),
        regime_confidence=float(coin_regime_info.get("confidence", 0)),
        strategy_score=float(strategy_score),
        risk_pct=risk_res.risk_pct,
        stop=None, target=None,
        decision=decision_type, reason=decision_reason,
        trace=trace_fields,
    )

    decisions.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "side": side,
        "strategy_score": round(float(strategy_score), 1),
        "regime_score": round(regime_score_val, 1),
        "coin_score": round(float(strategy_score), 1),
        "risk_score": round(risk_res.risk_pct * 100, 1),
        "final_score": round(final_score, 1),
        "category": category,
        "decision": decision_type,
        "reason": decision_reason,
    })

    # Otomatik PAPER modu — işlem aç
    if (approved and auto_paper and mode == "AUTO" and
            decision_type == "OPEN" and side is not None):
        _open_paper_trade(
            symbol=symbol, side=side, details=details,
            trading_state=trading_state,
            adaptive_cfg=adaptive_cfg,
            config=config,
            risk_pct=risk_res.risk_pct,
            coin_regime=coin_regime_info.get("regime", ""),
            final_score=final_score,
            reason=decision_reason,
        )


def _check_cooldown(symbol: str, trading_state: dict, cooldown_min: int) -> bool:
    """Son kapanıştan bu yana cooldown süresi geçti mi?"""
    trades = trading_state.get("trades", [])
    if not isinstance(trades, list):
        return True
    # Bu sembolde son kapanış
    sym_trades = [t for t in trades if t.get("symbol") == symbol]
    if not sym_trades:
        return True
    last_close = sym_trades[-1].get("closed_at")
    if not last_close:
        return True
    try:
        closed_dt = datetime.fromisoformat(last_close)
        elapsed   = (datetime.now(timezone.utc) - closed_dt).total_seconds() / 60
        return elapsed >= cooldown_min
    except Exception:
        return True


def _open_paper_trade(
    symbol: str, side: str, details: dict,
    trading_state: dict, adaptive_cfg: dict,
    config: dict, risk_pct: float,
    coin_regime: str, final_score: float, reason: str,
) -> None:
    """PAPER işlemi aç (alpha20 ile aynı mantık, genişletilmiş kayıt)."""
    import metrics_store as ms
    import adaptive_risk as ar
    import alpha20

    entry = float(details["price"])
    atr   = float(details["atr"])
    atr_mult = float(config.get("atr_stop_multiplier", 1.5))
    rr       = float(config.get("reward_risk_ratio", 2.0))

    # GÖREV 118 — Mission-11 ekonomi kapısı bu yolda da zorunlu:
    # alpha20.run_cycle aynı kuralı uyguluyordu; orkestre edilen AUTO
    # yolunda çağrı eksikti (fee-dominant işlem açılabiliyordu).
    # Kural/eşik AYNEN alpha20.evaluate_trade_economics'ten gelir.
    econ = alpha20.evaluate_trade_economics(
        entry, atr, side, float(trading_state["balance"]), config)
    if econ["skip"]:
        log.info(
            "SKIPPED: Expected fee exceeds acceptable threshold. | %s %s"
            " | brüt=%.4f fee=%.4f sf=%.1f",
            symbol, side, econ["expected_gross_profit"],
            econ["expected_total_fee"], econ["safety_factor"])
        ms.append_decision(
            symbol=symbol, price=entry, regime=coin_regime,
            regime_confidence=0, strategy_score=0,
            final_score=final_score, risk_pct=risk_pct,
            stop=None, target=None,
            decision="REJECT",
            reason="FEE_DRAG — beklenen brüt kâr, komisyon×güvenlik "
                   "katsayısını karşılamıyor (Mission-11 ekonomi kapısı)",
        )
        return

    qty, stop_dist, err = ar.calculate_position_size(
        balance=float(trading_state["balance"]),
        risk_pct=risk_pct, entry=entry, stop=0,
        atr=atr, atr_stop_multiplier=atr_mult,
        adaptive_cfg=adaptive_cfg,
    )
    if err or qty <= 0:
        log.warning("Pozisyon büyüklüğü hesaplanamadı (%s): %s", symbol, err)
        return

    stop, target, actual_rr = ar.calculate_targets(
        entry=entry, stop_distance=stop_dist,
        side=side, reward_risk=rr, adaptive_cfg=adaptive_cfg,
    )

    # Geçerli stop kontrolü
    if stop <= 0 or target <= 0:
        log.warning("Geçersiz stop/hedef (%s). İşlem açılmadı.", symbol)
        return

    risk_usdt = float(trading_state["balance"]) * risk_pct / 100
    pos = {
        "symbol": symbol, "side": side, "entry": entry,
        "stop": stop, "target": target, "quantity": qty,
        "risk_usdt": round(risk_usdt, 4),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        # Genişletilmiş alanlar
        "regime": coin_regime, "final_score": round(final_score, 2),
        "reason": reason, "atr": atr, "rr": actual_rr,
    }
    trading_state["position"] = pos
    _save_state(trading_state)

    ms.append_decision(
        symbol=symbol, price=entry, regime=coin_regime,
        regime_confidence=0, strategy_score=0,
        final_score=final_score, risk_pct=risk_pct,
        stop=stop, target=target,
        decision="OPEN", reason=reason,
    )
    log.info("AUTO PAPER AÇILDI | %s %s | giriş=%.4f stop=%.4f hedef=%.4f",
             symbol, side, entry, stop, target)


# ══════════════════════════════════════════════════════════════════════════════
# Dosya kilidi (iki instance engeli)
# ══════════════════════════════════════════════════════════════════════════════

_lock_fd = None

def _acquire_file_lock() -> bool:
    global _lock_fd
    try:
        _lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, BlockingIOError):
        return False


def _release_file_lock() -> None:
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        _lock_fd = None


# ══════════════════════════════════════════════════════════════════════════════
# Ana döngü
# ══════════════════════════════════════════════════════════════════════════════

def start_controller_loop() -> bool:
    """Arka planda otomatik döngüyü başlat. Zaten çalışıyorsa False döndür."""
    with _CONTROLLER_LOCK:
        if _LOOP_RUNNING.is_set():
            return False
        _LOOP_RUNNING.set()

    def _loop() -> None:
        import metrics_store as ms
        import learning_engine as le

        if not _acquire_file_lock():
            log.warning("Controller zaten çalışıyor (dosya kilidi alınamadı).")
            _LOOP_RUNNING.clear()
            return

        try:
            _update_status(running=True)
            cycle = 0
            while _LOOP_RUNNING.is_set():
                cycle += 1
                try:
                    cfg = _load_config()
                    if cfg is None:
                        log.warning("config.json okunamadı; döngü bekleniyor.")
                        time.sleep(30)
                        continue

                    ok, adaptive_cfg = _validate_adaptive_config(cfg)
                    if not ok:
                        log.warning("Adaptive config sorunlu; varsayılanlar kullanıldı.")

                    if not adaptive_cfg.get("enabled", False):
                        time.sleep(30)
                        continue

                    mode = adaptive_cfg.get("mode", "MONITOR")
                    _update_status(mode=mode, cycle_count=cycle,
                                   last_cycle_time=datetime.now(timezone.utc).isoformat())

                    symbols = cfg.get("symbols", ["BTCUSDT"])
                    # Dinamik evren (git dışı runtime store) ekleri
                    try:
                        import universe_manager as _um
                        symbols = _um.effective_symbols(symbols)
                    except Exception:
                        pass
                    _run_single_cycle(adaptive_cfg, symbols)
                    _update_status(
                        analyzed_symbol_count=len(symbols))

                    # FIX misyonu: başarılı çevrim Dynamic Universe
                    # yenilemesini GERÇEKTEN çağırır (ilk çevrimde
                    # hemen; sonrasında eval_interval_hours'a saygı).
                    try:
                        import universe_manager as um
                        ur = um.scheduled_refresh(symbols)
                        _update_status(last_universe_refresh=ur)
                    except Exception as exc:
                        log.warning(
                            "Evren yenileme çağrısı hatası: %s", exc)
                        _update_status(
                            last_universe_refresh="FAILED")

                    # Öğrenme motoru
                    learn_interval = float(adaptive_cfg.get("learning_interval_hours", 24))
                    last_learn     = _last_status.get("last_learning_time")
                    run_learning   = True
                    if last_learn:
                        try:
                            elapsed_h = (datetime.now(timezone.utc) -
                                        datetime.fromisoformat(last_learn)).total_seconds() / 3600
                            run_learning = elapsed_h >= learn_interval
                        except Exception:
                            pass
                    if run_learning:
                        try:
                            result = le.run_learning_update(adaptive_cfg)
                            if result:
                                _update_status(
                                    last_learning_time=datetime.now(timezone.utc).isoformat(),
                                    model_version=result.get("version", 1),
                                )
                        except Exception as exc:
                            log.warning("Öğrenme motoru hatası: %s", exc)

                    # Dual-model öğrenme köprüsü: her çevrimde UCUZ
                    # uygunluk kontrolü (interval VEYA yeni kapanan
                    # işlem eşiği) dual_learning içinde yapılır —
                    # ikinci paralel scheduler DEĞİLDİR.
                    try:
                        dl = le.run_dual_learning_update(adaptive_cfg)
                        if dl:
                            _update_status(
                                last_dual_learning=dl.get("ran_at"))
                    except Exception as exc:
                        log.warning(
                            "Dual öğrenme köprü hatası: %s", exc)

                    # Continuous Strategy Lab: dual_learning uzantısı,
                    # kendi interval/devre-kesici denetimi içinde —
                    # ikinci paralel scheduler DEĞİLDİR. LIVE ORDERS
                    # DISABLED — lab gerçek emir açamaz.
                    try:
                        sl = le.run_strategy_lab_cycle(adaptive_cfg)
                        if sl:
                            _update_status(
                                last_strategy_lab=sl.get("ran_at"))
                    except Exception as exc:
                        log.warning(
                            "Strategy Lab köprü hatası: %s", exc)

                    scan_s = RUNTIME_SCAN_SECONDS.get(
                        "value", int(cfg.get("scan_seconds", 60)))
                    _update_status(
                        last_cycle_error=None,
                        next_cycle_time=(
                            datetime.now(timezone.utc).isoformat()
                        ),
                    )
                    time.sleep(scan_s)

                except Exception as exc:
                    log.error("Döngü hatası: %s", exc)
                    _update_status(last_cycle_error=str(exc))
                    ms.append_system_error(component="auto_controller",
                                           error_type="LOOP_ERROR",
                                           message=str(exc)[:300])
                    time.sleep(30)
        finally:
            _update_status(running=False)
            _release_file_lock()
            _LOOP_RUNNING.clear()

    t = threading.Thread(target=_loop, daemon=True, name="auto_controller")
    t.start()
    return True


def stop_controller_loop() -> None:
    """Döngüyü durdur."""
    _LOOP_RUNNING.clear()


def is_running() -> bool:
    return _LOOP_RUNNING.is_set()
