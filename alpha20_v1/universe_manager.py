"""
universe_manager.py — Akıllı Coin Seçimi motoru (PAPER modu).

Binance herkese açık USD-M Futures API'sini kullanarak aktif USDT
perpetual sözleşmelerini puanlar ve coin listesini yönetir.
API anahtarı, canlı emir veya gerçek para işlemi içermez.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

log = logging.getLogger("universe_manager")

ROOT = Path(__file__).resolve().parent          # alpha20_v1/
SMART_CONFIG_PATH = ROOT / "smart_config.json"   # SEED (izlenen)
SMART_LOG_PATH    = ROOT / "smart_changes.json"  # legacy log (salt okur)
RUNTIME_STORE_PATH = ROOT / "universe_runtime.json"  # git dışı kanonik
CONFIG_PATH       = ROOT / "config.json"
STATE_PATH        = ROOT / "state.json"
BASE_URL          = "https://fapi.binance.com"

_ANALYSIS_LOCK    = threading.Lock()
_ANALYSIS_RUNNING = threading.Event()

# ── Filtre sabit değerleri ─────────────────────────────────────────────────────
EXCLUDED_KEYWORDS: frozenset[str] = frozenset(["UP", "DOWN", "BULL", "BEAR"])
LEVERAGED_SUFFIXES: frozenset[str] = frozenset(["2L", "2S", "3L", "3S", "5L", "5S"])
MIN_VOLUME_USDT   = 10_000_000   # $10M
MAX_CANDIDATES    = 50           # hacme göre sıralı en iyi N aday
KLINE_LIMIT       = 100
MIN_CANDLES       = 55
LOG_MAX           = 100

# Temel semboller — HER ZAMAN evrende kalır (dinamik seçim çıkaramaz).
BASE_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
# Dinamik evren üst sınırı (temel semboller dahil toplam).
HARD_MAX_COINS = 20

# ── Akıllı seçim varsayılanları ────────────────────────────────────────────────
SMART_DEFAULTS: dict[str, Any] = {
    "mode":                "MANUEL",   # MANUEL | ONERI | OTOMATIK
    "max_coins":           10,
    "min_coins":           3,
    "eval_interval_hours": 6,
    "add_threshold":       70,
    "remove_threshold":    50,
    "min_hold_hours":      24,
    "cooldown_hours":      12,
    "anchor_symbol":       "BTCUSDT",
    "pinned":              [],
    "manual_list":         None,       # mod değiştirildiğinde anlık liste buraya kaydedilir
    "last_analysis_time":  None,
    "analysis_running":    False,
    "candidate_count":     0,
    "last_suggestions":    [],
    "coin_history":        {},         # sembol → {added_at, removed_at}
    "last_auto_change":    None,
}

# global durum — app.py tarafından okunur
analysis_status: dict[str, Any] = {"running": False, "error": None, "started_at": None}
_status_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# Config yardımcıları
# ══════════════════════════════════════════════════════════════════════════════

def _atomic_write(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def get_smart_config() -> dict[str, Any]:
    """SEED (smart_config.json) + git dışı runtime overlay birleşimi.

    Runtime yazımları overlay'de yaşadığı için tracked seed hiç
    kirletilmez; overlay varsa seed'i alan bazında ezer."""
    merged = dict(SMART_DEFAULTS)
    try:
        if SMART_CONFIG_PATH.exists():
            with SMART_CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged.update(data)
    except (OSError, json.JSONDecodeError):
        pass
    overlay = _load_runtime().get("smart")
    if isinstance(overlay, dict):
        merged.update(overlay)
    return _enforce_universe_contract(merged)


def _enforce_universe_contract(cfg: dict[str, Any]) -> dict[str, Any]:
    """Misyon sözleşmesi (yükleme anında, dosyaya YAZMADAN):

    - BTC/ETH/SOL her zaman pinned — dinamik seçim çıkaramaz.
    - Toplam evren en fazla HARD_MAX_COINS (20) sembol."""
    pinned = list(cfg.get("pinned") or [])
    for sym in BASE_SYMBOLS:
        if sym not in pinned:
            pinned.append(sym)
    cfg["pinned"] = pinned
    try:
        max_coins = int(cfg.get("max_coins", 10))
    except (TypeError, ValueError):
        max_coins = 10
    cfg["max_coins"] = max(len(BASE_SYMBOLS),
                           min(HARD_MAX_COINS, max_coins))
    return cfg


def save_smart_config(cfg: dict[str, Any]) -> None:
    """Runtime smart-config yazımı GIT DIŞI kanonik store'a gider.

    smart_config.json artık yalnız SEED (izlenen, elle düzenlenen
    varsayılan); normal çalışma onu ASLA yazmaz → git status temiz."""
    rt = _load_runtime()
    rt["smart"] = cfg
    _save_runtime(rt)


def load_main_config() -> dict[str, Any]:
    """config.json (izlenen) + runtime dynamic_symbols birleşimi."""
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(cfg.get("symbols"), list):
        cfg["symbols"] = effective_symbols(cfg["symbols"])
    return cfg


def effective_symbols(base_symbols: list[str]) -> list[str]:
    """Etkin evren = config.json tabanı + runtime dinamik ekler
    (sıra korunur, tekrarsız, HARD_MAX_COINS tavanı)."""
    rt = _load_runtime()
    removed = set(rt.get("removed_symbols") or []) - set(BASE_SYMBOLS)
    merged = [s for s in base_symbols if s not in removed]
    for sym in rt.get("dynamic_symbols", []):
        if isinstance(sym, str) and sym not in merged:
            merged.append(sym)
    return merged[:HARD_MAX_COINS]


def save_main_config(data: dict[str, Any]) -> None:
    _atomic_write(CONFIG_PATH, data)


# ── Git dışı kanonik runtime store ──────────────────────────────────
# Dynamic Universe'in TÜM runtime durumu (smart overlay, dinamik
# semboller, değişiklik log'u) burada yaşar; izlenen dosyalara normal
# çalışmada yazılmaz.

def _load_runtime() -> dict[str, Any]:
    if not RUNTIME_STORE_PATH.exists():
        return {}
    try:
        with RUNTIME_STORE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_runtime(data: dict[str, Any]) -> None:
    _atomic_write(RUNTIME_STORE_PATH, data)


def get_smart_log() -> list[dict]:
    """Runtime log (git dışı) + legacy smart_changes.json (salt okur)."""
    entries = list(_load_runtime().get("log", []))
    if SMART_LOG_PATH.exists():
        try:
            with SMART_LOG_PATH.open("r", encoding="utf-8") as f:
                legacy = json.load(f)
            if isinstance(legacy, list):
                entries.extend(legacy)
        except (OSError, json.JSONDecodeError):
            pass
    return entries[:LOG_MAX]


def _append_smart_log(entry: dict) -> None:
    """Yeni log kayıtları YALNIZ git dışı runtime store'a yazılır."""
    rt = _load_runtime()
    log_entries = list(rt.get("log", []))
    log_entries.insert(0, entry)
    rt["log"] = log_entries[:LOG_MAX]
    _save_runtime(rt)


def _load_trades() -> list[dict]:
    try:
        if not STATE_PATH.exists():
            return []
        with STATE_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
        trades = state.get("trades", [])
        return trades if isinstance(trades, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def get_open_position_symbol() -> str | None:
    try:
        if not STATE_PATH.exists():
            return None
        with STATE_PATH.open("r", encoding="utf-8") as f:
            state = json.load(f)
        pos = state.get("position")
        return pos.get("symbol") if isinstance(pos, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Binance API çağrıları (yalnızca herkese açık uç noktalar)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch(url: str, params: dict | None = None, timeout: int = 12, retries: int = 2) -> Any:
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("Fetch başarısız.")


def get_usdt_perp_symbols() -> list[str]:
    """Aktif USDT perpetual futures sembollerini döndür; kaldıraçlı tokenleri dışla."""
    data = _fetch(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=20)
    symbols = []
    for s in data.get("symbols", []):
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("status") != "TRADING":
            continue
        sym  = s["symbol"]
        base = s.get("baseAsset", "")
        if any(kw in base for kw in EXCLUDED_KEYWORDS):
            continue
        if any(base.endswith(sfx) for sfx in LEVERAGED_SUFFIXES):
            continue
        symbols.append(sym)
    return symbols


def get_24h_tickers() -> dict[str, dict]:
    """24 saatlik ticker verilerini döndür."""
    data = _fetch(f"{BASE_URL}/fapi/v1/ticker/24hr", timeout=20)
    result: dict[str, dict] = {}
    for item in (data if isinstance(data, list) else []):
        sym = item.get("symbol", "")
        if sym:
            result[sym] = item
    return result


def fetch_klines(symbol: str, interval: str, limit: int = KLINE_LIMIT) -> pd.DataFrame | None:
    try:
        rows = _fetch(
            f"{BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=12,
        )
    except Exception:
        return None
    if not rows or len(rows) < MIN_CANDLES:
        return None
    cols = [
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_base","taker_quote","ignore",
    ]
    df = pd.DataFrame(rows, columns=cols)
    for col in ["open","high","low","close","volume","quote_vol"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


# ══════════════════════════════════════════════════════════════════════════════
# Gösterge hesaplamaları (alpha20.py ile aynı formüller)
# ══════════════════════════════════════════════════════════════════════════════

def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]
    df["ema20"]    = close.ewm(span=20,  adjust=False).mean()
    df["ema50"]    = close.ewm(span=50,  adjust=False).mean()
    df["ema200"]   = close.ewm(span=200, adjust=False).mean()
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs    = gain / loss.replace(0, float("nan"))
    df["rsi14"]    = 100 - (100 / (1 + rs))
    prev_close     = close.shift(1)
    tr = pd.concat(
        [df["high"]-df["low"],
         (df["high"]-prev_close).abs(),
         (df["low"]-prev_close).abs()],
        axis=1,
    ).max(axis=1)
    df["atr14"]    = tr.ewm(alpha=1/14, adjust=False).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Puanlama bileşenleri
# ══════════════════════════════════════════════════════════════════════════════

def _vol_score(vol: float) -> int:
    if vol < 20_000_000:   return 5
    if vol < 100_000_000:  return 10
    if vol < 500_000_000:  return 15
    return 20

def _vol_ratio_score(ratio: float) -> int:
    if ratio < 0.7:  return 0
    if ratio < 1.0:  return 5
    if ratio < 1.2:  return 10
    if ratio < 1.5:  return 13
    return 15

def _atr_score(atr_pct: float) -> int:
    if atr_pct < 0.3:  return 2
    if atr_pct < 0.7:  return 8
    if atr_pct < 1.5:  return 15
    if atr_pct < 3.0:  return 10
    if atr_pct < 5.0:  return 5
    return 2

def _trend_score(f: pd.Series, t: pd.Series) -> int:
    f_bull = bool(f["ema20"] > f["ema50"] and f["close"] > f["ema20"])
    f_bear = bool(f["ema20"] < f["ema50"] and f["close"] < f["ema20"])
    t_bull = bool(t["ema50"] > t["ema200"] and t["close"] > t["ema50"])
    t_bear = bool(t["ema50"] < t["ema200"] and t["close"] < t["ema50"])
    if (f_bull and t_bull) or (f_bear and t_bear):
        return 20
    if f_bull or t_bull or f_bear or t_bear:
        return 12
    return 5

def _trend_label(f: pd.Series, t: pd.Series) -> str:
    f_bull = bool(f["ema20"] > f["ema50"] and f["close"] > f["ema20"])
    f_bear = bool(f["ema20"] < f["ema50"] and f["close"] < f["ema20"])
    t_bull = bool(t["ema50"] > t["ema200"] and t["close"] > t["ema50"])
    t_bear = bool(t["ema50"] < t["ema200"] and t["close"] < t["ema50"])
    if f_bull and t_bull:  return "YUKARI"
    if f_bear and t_bear:  return "ASAGI"
    return "KARISIK"

def _ema_slope_score(df: pd.DataFrame) -> int:
    if len(df) < 6:
        return 3
    e_now  = df["ema20"].iloc[-1]
    e_prev = df["ema20"].iloc[-6]
    if e_prev == 0:
        return 3
    slope = abs(e_now - e_prev) / e_prev * 100
    if slope > 0.5:  return 10
    if slope > 0.2:  return 7
    return 3

def _rsi_score(rsi: float) -> int:
    if 35 <= rsi <= 65: return 10
    if (30 <= rsi < 35) or (65 < rsi <= 70): return 7
    if (25 <= rsi < 30) or (70 < rsi <= 75): return 4
    return 1

def _regularity_score(df: pd.DataFrame) -> int:
    if len(df) < 20:
        return 2
    close = df["close"].iloc[-20:]
    ema20 = df["ema20"].iloc[-20:]
    consistent = int(((close - ema20).abs() / close < 0.015).sum())
    if consistent >= 14: return 5
    if consistent >= 8:  return 3
    return 1

def _data_quality_score(df: pd.DataFrame | None) -> int:
    if df is None: return 0
    n = len(df)
    if n >= 80: return 5
    if n >= 55: return 3
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# Piyasa Fırsat Puanı (0-100)
# ══════════════════════════════════════════════════════════════════════════════

def calc_market_score(
    symbol: str,
    ticker: dict,
    df15: pd.DataFrame | None,
    df1h:  pd.DataFrame | None,
) -> dict[str, Any]:
    comp: dict[str, int] = {}
    vol24h = float(ticker.get("quoteVolume", 0) or 0)
    comp["volume"] = _vol_score(vol24h)

    if df15 is None or df1h is None or len(df15) < MIN_CANDLES:
        comp["data_quality"] = _data_quality_score(df15)
        return {
            "market_score": min(100, sum(comp.values())),
            "volume_24h": vol24h, "atr_pct": 0.0,
            "trend": "VERİ YOK", "rsi": 50.0, "components": comp,
        }

    df15 = _add_indicators(df15)
    df1h = _add_indicators(df1h)
    f = df15.iloc[-2]
    t = df1h.iloc[-2]

    vol_ratio = float(f["volume"] / f["vol_ma20"]) if (f["vol_ma20"] and f["vol_ma20"] > 0) else 0.0
    atr_pct   = float(f["atr14"] / f["close"] * 100) if f["close"] > 0 else 0.0
    rsi       = float(f["rsi14"]) if not math.isnan(float(f["rsi14"])) else 50.0

    comp["vol_ratio"]     = _vol_ratio_score(vol_ratio)
    comp["volatility"]    = _atr_score(atr_pct)
    comp["trend"]         = _trend_score(f, t)
    comp["ema_slope"]     = _ema_slope_score(df15)
    comp["rsi"]           = _rsi_score(rsi)
    comp["regularity"]    = _regularity_score(df15)
    comp["data_quality"]  = _data_quality_score(df15)

    return {
        "market_score": min(100, sum(comp.values())),
        "volume_24h":   vol24h,
        "atr_pct":      round(atr_pct, 3),
        "trend":        _trend_label(f, t),
        "rsi":          round(rsi, 2),
        "components":   comp,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PAPER Performans Puanı (0-100, toplam puanın en fazla %30'u)
# ══════════════════════════════════════════════════════════════════════════════

def calc_paper_score(symbol: str, trades: list[dict]) -> dict[str, Any]:
    sym_trades = [t for t in trades if t.get("symbol") == symbol]
    n = len(sym_trades)

    if n < 5:
        return {"paper_score": 50, "trade_count": n, "win_rate": None, "total_pnl": None, "reliable": False}

    # Yakın tarihli işlemlere daha fazla ağırlık ver (üstel azalma)
    weights = [0.5 ** (i * 0.2) for i in range(n - 1, -1, -1)]
    w_total = sum(weights)

    w_wins    = sum(w for t, w in zip(sym_trades, weights) if t.get("result") == "WIN")
    w_win_rate = w_wins / w_total * 100

    total_pnl  = sum(float(t.get("pnl", 0) or 0) for t in sym_trades)
    gross_win  = sum(abs(float(t.get("pnl", 0) or 0)) for t in sym_trades if float(t.get("pnl", 0) or 0) > 0)
    gross_loss = sum(abs(float(t.get("pnl", 0) or 0)) for t in sym_trades if float(t.get("pnl", 0) or 0) < 0)
    pf = gross_win / gross_loss if gross_loss > 0 else (2.0 if gross_win > 0 else 1.0)

    if w_win_rate >= 70:   base = 85
    elif w_win_rate >= 60: base = 70
    elif w_win_rate >= 50: base = 55
    elif w_win_rate >= 40: base = 35
    else:                  base = 20

    if pf > 2.0:   base = min(100, base + 10)
    elif pf > 1.5: base = min(100, base + 5)
    elif pf < 0.8: base = max(0,   base - 10)

    # Örnek büyüklüğüne göre nötrle (50) harmanlama
    reliability = min(1.0, n / 20)
    score = int(base * reliability + 50 * (1 - reliability))

    return {
        "paper_score": max(0, min(100, score)),
        "trade_count": n,
        "win_rate":    round(w_win_rate, 1),
        "total_pnl":   round(total_pnl, 4),
        "reliable":    n >= 10,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tam analiz
# ══════════════════════════════════════════════════════════════════════════════

def run_analysis(current_symbols: list[str], smart_cfg: dict) -> list[dict]:
    """Evreni analiz et ve sıralı aday listesi döndür (senkron; arka plan iş parçacığından çağır)."""
    log.info("Evren analizi başladı.")

    try:
        all_syms = get_usdt_perp_symbols()
    except Exception as exc:
        log.error("ExchangeInfo alınamadı: %s", exc)
        return []

    try:
        tickers = get_24h_tickers()
    except Exception as exc:
        log.error("24h ticker alınamadı: %s", exc)
        return []

    candidates = []
    for sym in all_syms:
        ticker = tickers.get(sym, {})
        vol = float(ticker.get("quoteVolume", 0) or 0)
        if vol >= MIN_VOLUME_USDT:
            candidates.append((vol, sym, ticker))

    candidates.sort(reverse=True)
    candidates = candidates[:MAX_CANDIDATES]
    log.info("%d aday puanlanıyor.", len(candidates))

    trades  = _load_trades()
    pinned  = set(smart_cfg.get("pinned", []))
    results = []

    for vol, symbol, ticker in candidates:
        try:
            df15 = fetch_klines(symbol, "15m")
            df1h = fetch_klines(symbol, "1h")
            mkt  = calc_market_score(symbol, ticker, df15, df1h)
            ppr  = calc_paper_score(symbol, trades)
            total = round(mkt["market_score"] * 0.7 + ppr["paper_score"] * 0.3)
            in_list  = symbol in current_symbols
            is_pinned = symbol in pinned

            if in_list:
                if is_pinned:
                    action = "TUT"; reason = "Sabitlenmiş; otomatik çıkarma engellendi."
                elif total >= 60:
                    action = "TUT"; reason = "Strateji koşullarına uygun izleme önerisi."
                else:
                    action = "ÇIKAR"; reason = "Yeterli puan alınamadı; çıkarma önerisi."
            else:
                if total >= 75:
                    action = "EKLE"; reason = "Yüksek puanlı aday; izleme listesine ekleme önerisi."
                elif total >= 60:
                    action = "İZLE"; reason = "Orta düzey puan; gelecek analizde takip edilmeli."
                else:
                    action = "GEÇ"; reason = "Şu an için yeterli koşul oluşmadı."

            results.append({
                "symbol":       symbol,
                "total_score":  total,
                "market_score": mkt["market_score"],
                "paper_score":  ppr["paper_score"],
                "volume_24h":   mkt["volume_24h"],
                "atr_pct":      mkt["atr_pct"],
                "trend":        mkt["trend"],
                "rsi":          mkt.get("rsi", 50.0),
                "trade_count":  ppr["trade_count"],
                "win_rate":     ppr["win_rate"],
                "total_pnl":    ppr["total_pnl"],
                "action":       action,
                "reason":       reason,
                "in_list":      in_list,
                "pinned":       is_pinned,
                "components":   mkt.get("components", {}),
            })
        except Exception as exc:
            log.warning("%s puanlanamadı: %s", symbol, exc)
            continue

    results.sort(key=lambda x: x["total_score"], reverse=True)
    log.info("Analiz tamamlandı. %d aday puanlandı.", len(results))
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Otomatik seçim kuralları
# ══════════════════════════════════════════════════════════════════════════════

def compute_auto_changes(
    suggestions: list[dict],
    current_symbols: list[str],
    smart_cfg: dict,
) -> tuple[list[str], list[str]]:
    add_threshold    = int(smart_cfg.get("add_threshold", 70))
    remove_threshold = int(smart_cfg.get("remove_threshold", 50))
    max_coins        = int(smart_cfg.get("max_coins", 10))
    min_coins        = int(smart_cfg.get("min_coins", 3))
    min_hold_hours   = float(smart_cfg.get("min_hold_hours", 24))
    cooldown_hours   = float(smart_cfg.get("cooldown_hours", 12))
    anchor           = smart_cfg.get("anchor_symbol", "BTCUSDT")
    pinned           = set(smart_cfg.get("pinned", []))
    coin_history     = smart_cfg.get("coin_history", {})
    now              = datetime.now(timezone.utc)
    open_pos         = get_open_position_symbol()
    by_sym           = {s["symbol"]: s for s in suggestions}

    to_remove: list[str] = []
    for sym in current_symbols:
        if sym == anchor or sym in pinned or sym == open_pos:
            continue
        info = by_sym.get(sym)
        if info is None or info["total_score"] >= remove_threshold:
            continue
        hist      = coin_history.get(sym, {})
        added_at  = hist.get("added_at")
        if added_at:
            try:
                elapsed = (now - datetime.fromisoformat(added_at)).total_seconds() / 3600
                if elapsed < min_hold_hours:
                    continue
            except Exception:
                pass
        to_remove.append(sym)

    available = max_coins - (len(current_symbols) - len(to_remove))
    to_add: list[str] = []
    for item in suggestions:
        if len(to_add) >= available:
            break
        sym = item["symbol"]
        if (sym in current_symbols and sym not in to_remove) or item["total_score"] < add_threshold:
            continue
        hist       = coin_history.get(sym, {})
        removed_at = hist.get("removed_at")
        if removed_at:
            try:
                elapsed = (now - datetime.fromisoformat(removed_at)).total_seconds() / 3600
                if elapsed < cooldown_hours:
                    continue
            except Exception:
                pass
        to_add.append(sym)

    # Tek değerlendirmede listanın en fazla %30'u değişebilir
    max_change    = max(1, int(len(current_symbols) * 0.30))
    total_changes = len(to_add) + len(to_remove)
    if total_changes > max_change:
        to_remove = sorted(to_remove, key=lambda s: by_sym.get(s, {}).get("total_score", 0))[:max_change]
        to_add    = to_add[: max(0, max_change - len(to_remove))]

    # Minimum coin sayısını koru
    new_count = len(current_symbols) - len(to_remove) + len(to_add)
    while to_remove and new_count < min_coins:
        to_remove.pop()
        new_count += 1

    return to_add, to_remove


def apply_auto_changes(
    to_add: list[str],
    to_remove: list[str],
    smart_cfg: dict,
    mode: str,
) -> tuple[bool, str]:
    if not to_add and not to_remove:
        return True, "Değişiklik gerekmedi."

    now_iso  = datetime.now(timezone.utc).isoformat()
    main_cfg = load_main_config()
    if not main_cfg:
        return False, "config.json okunamadı."

    current   = list(main_cfg.get("symbols", []))
    prev_list = list(current)
    for sym in to_remove:
        if sym in current:
            current.remove(sym)
    for sym in to_add:
        if sym not in current:
            current.append(sym)

    # Dinamik evren durumu GIT DIŞI store'a yazılır — config.json
    # (izlenen taban) normal çalışmada değiştirilmez, git status temiz.
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            base_syms = json.load(f).get("symbols", [])
    except (OSError, json.JSONDecodeError):
        base_syms = []
    try:
        rt = _load_runtime()
        rt["dynamic_symbols"] = [s for s in current
                                 if s not in base_syms]
        rt["removed_symbols"] = [s for s in base_syms
                                 if s not in current
                                 and s not in BASE_SYMBOLS]
        _save_runtime(rt)
    except OSError as exc:
        return False, f"universe_runtime.json kaydedilemedi: {exc}"

    coin_history = dict(smart_cfg.get("coin_history", {}))
    for sym in to_remove:
        coin_history[sym] = {**(coin_history.get(sym) or {}), "removed_at": now_iso}
    for sym in to_add:
        coin_history[sym] = {**(coin_history.get(sym) or {}), "added_at": now_iso, "removed_at": None}
    smart_cfg["coin_history"]   = coin_history
    smart_cfg["last_auto_change"] = now_iso

    entry = {
        "timestamp": now_iso, "mode": mode,
        "added": to_add, "removed": to_remove,
        "prev_list": prev_list, "new_list": current,
        "reason": f"{len(to_add)} eklendi, {len(to_remove)} çıkarıldı.",
    }
    _append_smart_log(entry)

    parts = []
    if to_add:    parts.append(f"Eklendi: {', '.join(to_add)}")
    if to_remove: parts.append(f"Çıkarıldı: {', '.join(to_remove)}")
    return True, "; ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler kaynaklı evren yenileme (FIX misyonu)
# ══════════════════════════════════════════════════════════════════════════════

def get_scheduler_refresh_status() -> dict[str, Any]:
    """Zamanlayıcı kaynaklı son evren yenilemesinin dosya-tabanlı
    durumu (worker'lar arası ortak)."""
    cfg = get_smart_config()
    sr = cfg.get("scheduler_refresh")
    return sr if isinstance(sr, dict) else {}


def scheduled_refresh(current_symbols: list[str]) -> str:
    """Zamanlayıcı çevriminden çağrılır (senkron, aynı thread).

    - İlk uygun çevrimde HER ZAMAN koşar (NOT_RUN_YET burada temizlenir).
    - Sonrasında eval_interval_hours'a saygı duyar (SKIPPED_RECENT).
    - Önerileri gerçekten uygular: BTC/ETH/SOL pinned korunur,
      HARD_MAX_COINS (20) tavanı contract ile zorlanır.
    - Sonuç smart_config['scheduler_refresh'] içine yazılır:
      COMPLETED ya da açık hata kodu — sessiz başarı yok."""
    now_iso = datetime.now(timezone.utc).isoformat()

    def _record(result: str, error_code: str | None) -> str:
        cfg2 = get_smart_config()
        cfg2["scheduler_refresh"] = {
            "last_attempt_time": now_iso,
            "last_result": result,
            "last_error_code": error_code,
        }
        save_smart_config(cfg2)
        return result

    cfg = get_smart_config()
    last = cfg.get("last_analysis_time")
    if last:
        try:
            interval_h = float(cfg.get("eval_interval_hours", 6))
            elapsed_h = (datetime.now(timezone.utc) -
                         datetime.fromisoformat(last)
                         ).total_seconds() / 3600
            if elapsed_h < interval_h:
                return "SKIPPED_RECENT"  # taze; durum ezilmez
        except (TypeError, ValueError):
            pass
    if not _ANALYSIS_LOCK.acquire(blocking=False):
        return "ALREADY_RUNNING"
    _ANALYSIS_RUNNING.set()
    try:
        with _status_lock:
            analysis_status.update({"running": True, "error": None,
                                    "started_at": now_iso})
        suggestions = run_analysis(current_symbols, cfg)
        cfg = get_smart_config()
        cfg["last_analysis_time"] = now_iso
        cfg["candidate_count"] = len(suggestions)
        cfg["last_suggestions"] = suggestions[:60]
        save_smart_config(cfg)
        to_add, to_remove = compute_auto_changes(
            suggestions, current_symbols, cfg)
        if to_add or to_remove:
            ok, msg = apply_auto_changes(
                to_add, to_remove, cfg, "SCHEDULER")
            # apply, cfg'yi (coin_history, last_auto_change) mutasyona
            # uğratır ama kaydetmez — scheduler yolunda burada kalıcıla
            save_smart_config(cfg)
            if not ok:
                log.error("Evren değişikliği uygulanamadı: %s", msg)
                return _record("FAILED", "UNIVERSE_APPLY_FAILED")
        return _record("COMPLETED", None)
    except Exception as exc:  # açık hata kodu — sessiz geçilmez
        log.error("Scheduler evren yenilemesi hatası: %s", exc)
        with _status_lock:
            analysis_status["error"] = str(exc)
        return _record("FAILED", "UNIVERSE_REFRESH_FAILED")
    finally:
        with _status_lock:
            analysis_status["running"] = False
        _ANALYSIS_RUNNING.clear()
        _ANALYSIS_LOCK.release()


# ══════════════════════════════════════════════════════════════════════════════
# Arka plan analiz tetikleyici
# ══════════════════════════════════════════════════════════════════════════════

def trigger_analysis(
    current_symbols: list[str],
    smart_cfg: dict,
    apply_if_auto: bool = False,
) -> bool:
    """Arka planda analiz başlat. Zaten çalışıyorsa False döndür."""
    if not _ANALYSIS_LOCK.acquire(blocking=False):
        return False
    if _ANALYSIS_RUNNING.is_set():
        _ANALYSIS_LOCK.release()
        return False

    def _run() -> None:
        _ANALYSIS_RUNNING.set()
        with _status_lock:
            analysis_status.update({"running": True, "error": None,
                                    "started_at": datetime.now(timezone.utc).isoformat()})
        cfg = get_smart_config()
        cfg["analysis_running"] = True
        save_smart_config(cfg)
        try:
            suggestions = run_analysis(current_symbols, smart_cfg)
            cfg = get_smart_config()
            cfg["analysis_running"]  = False
            cfg["last_analysis_time"] = datetime.now(timezone.utc).isoformat()
            cfg["candidate_count"]   = len(suggestions)
            cfg["last_suggestions"]  = suggestions[:60]
            if apply_if_auto and cfg.get("mode") == "OTOMATIK":
                to_add, to_remove = compute_auto_changes(suggestions, current_symbols, cfg)
                if to_add or to_remove:
                    apply_auto_changes(to_add, to_remove, cfg, "OTOMATIK")
            save_smart_config(cfg)
        except Exception as exc:
            log.error("Analiz hatası: %s", exc)
            with _status_lock:
                analysis_status["error"] = str(exc)
            cfg = get_smart_config()
            cfg["analysis_running"] = False
            save_smart_config(cfg)
        finally:
            _ANALYSIS_RUNNING.clear()
            _ANALYSIS_LOCK.release()
            with _status_lock:
                analysis_status["running"] = False

    threading.Thread(target=_run, daemon=True, name="universe_analysis").start()
    return True


def start_auto_loop(get_main_config_fn: Any) -> None:
    """Otomatik mod için periyodik analiz döngüsü (arka planda)."""
    def _loop() -> None:
        while True:
            time.sleep(60)
            try:
                cfg = get_smart_config()
                if cfg.get("mode") != "OTOMATIK":
                    continue
                if cfg.get("analysis_running") or _ANALYSIS_RUNNING.is_set():
                    continue
                last = cfg.get("last_analysis_time")
                if last:
                    last_dt     = datetime.fromisoformat(last)
                    interval_h  = float(cfg.get("eval_interval_hours", 6))
                    elapsed_h   = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                    if elapsed_h < interval_h:
                        continue
                main_cfg = get_main_config_fn()
                current  = main_cfg.get("symbols", [])
                trigger_analysis(current, cfg, apply_if_auto=True)
            except Exception as exc:
                log.warning("Otomatik döngü hatası: %s", exc)

    threading.Thread(target=_loop, daemon=True, name="auto_analysis").start()


# ══════════════════════════════════════════════════════════════════════════════
# Performans karşılaştırması
# ══════════════════════════════════════════════════════════════════════════════

def get_performance_comparison(trades: list[dict], manual_list: list[str] | None) -> dict[str, Any]:
    def _stats(batch: list[dict]) -> dict:
        n = len(batch)
        if n < 5:
            return {"trade_count": n, "insufficient": True}
        wins      = [t for t in batch if t.get("result") == "WIN"]
        win_rate  = len(wins) / n * 100
        net_pnl   = sum(float(t.get("pnl", 0) or 0) for t in batch)
        gw = sum(abs(float(t.get("pnl", 0) or 0)) for t in batch if float(t.get("pnl", 0) or 0) > 0)
        gl = sum(abs(float(t.get("pnl", 0) or 0)) for t in batch if float(t.get("pnl", 0) or 0) < 0)
        pf = round(gw / gl, 3) if gl > 0 else None
        # Maksimum düşüş (basit kümülatif)
        running = 0.0; peak = 0.0; max_dd = 0.0
        for t in batch:
            running += float(t.get("pnl", 0) or 0)
            peak     = max(peak, running)
            max_dd   = max(max_dd, peak - running)
        return {
            "trade_count":   n,
            "win_rate":      round(win_rate, 1),
            "net_pnl":       round(net_pnl, 4),
            "profit_factor": pf,
            "max_drawdown":  round(max_dd, 4),
            "insufficient":  False,
        }

    if manual_list:
        manual_trades = [t for t in trades if t.get("symbol") in manual_list]
        auto_trades   = [t for t in trades if t.get("symbol") not in manual_list]
    else:
        manual_trades = trades
        auto_trades   = []

    return {
        "manual": _stats(manual_trades),
        "auto":   _stats(auto_trades) if auto_trades else {"trade_count": 0, "insufficient": True},
    }


def fmt_volume(vol: float) -> str:
    if vol >= 1_000_000_000:
        return f"${vol/1_000_000_000:.2f}B"
    if vol >= 1_000_000:
        return f"${vol/1_000_000:.1f}M"
    return f"${vol:,.0f}"


def next_analysis_str(smart_cfg: dict) -> str:
    last = smart_cfg.get("last_analysis_time")
    if not last or smart_cfg.get("mode") != "OTOMATIK":
        return "—"
    try:
        last_dt    = datetime.fromisoformat(last)
        interval_h = float(smart_cfg.get("eval_interval_hours", 6))
        next_dt    = last_dt + timedelta(hours=interval_h)
        return next_dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "—"
