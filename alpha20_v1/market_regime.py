"""
market_regime.py — Binance herkese açık verileriyle piyasa rejimi tespiti.
API anahtarı veya gerçek emir içermez.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
log = logging.getLogger("market_regime")

# Rejim sabitleri
REGIME_STRONG_UP    = "Güçlü Yükseliş"
REGIME_WEAK_UP      = "Zayıf Yükseliş"
REGIME_STRONG_DOWN  = "Güçlü Düşüş"
REGIME_WEAK_DOWN    = "Zayıf Düşüş"
REGIME_SIDEWAYS     = "Yatay"
REGIME_HIGH_VOL     = "Yüksek Volatilite"
REGIME_LOW_VOL      = "Düşük Volatilite"
REGIME_UNCLEAR      = "Belirsiz"
REGIME_INSUFFICIENT = "Veri Yetersiz"

# İşlem için uygun rejimler
SUITABLE_REGIMES = {
    REGIME_STRONG_UP, REGIME_WEAK_UP,
    REGIME_STRONG_DOWN, REGIME_WEAK_DOWN,
}


def _fetch_klines(symbol: str, interval: str, limit: int = 100,
                  retries: int = 2, timeout: int = 12) -> pd.DataFrame | None:
    for attempt in range(retries + 1):
        import alpha20
        remaining = alpha20.rate_limit_remaining()
        if remaining > 0:
            log.warning(
                "GERİ ÇEKİLME | %s %s | Yeni istek atılmadı (%.0f saniye "
                "kaldı). %s", symbol, interval, remaining,
                alpha20.rate_limit_reason())
            return None
        try:
            r = requests.get(
                f"{BASE_URL}/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=timeout,
            )
            r.raise_for_status()
            rows = r.json()
            if not rows or len(rows) < 30:
                return None
            cols = ["open_time","open","high","low","close","volume",
                    "close_time","quote_vol","trades","taker_base","taker_quote","ignore"]
            df = pd.DataFrame(rows, columns=cols)
            for c in ["open","high","low","close","volume","quote_vol"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            return df.dropna(subset=["close"])
        except requests.exceptions.SSLError as exc:
            import alpha20
            log.warning("SSL DOĞRULAMA HATASI | %s %s | %s",
                        symbol, interval, alpha20.diagnose_ssl_error(exc))
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        except requests.exceptions.HTTPError as exc:
            import alpha20
            status = getattr(getattr(exc, "response", None),
                             "status_code", None)
            if status in (429, 418):
                alpha20.register_rate_limit(
                    int(status), getattr(exc, "response", None))
                # Geri çekilme sırasında yeniden denemek yasağı büyütür.
                return None
            log.warning("HTTP HATASI | %s %s | %s",
                        symbol, interval, alpha20.diagnose_http_error(exc))
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            import alpha20
            log.warning("AĞ HATASI | %s %s | %s",
                        symbol, interval, alpha20.diagnose_network_error(exc))
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        except Exception:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return None


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def _adx_like(df: pd.DataFrame, period: int = 14) -> float:
    """Basitleştirilmiş trend gücü (0-100). ADX'e benzer ama sadece yön tutarlılığı."""
    closes  = df["close"].values[-period * 2:]
    if len(closes) < period + 1:
        return 50.0
    returns = np.diff(closes)
    pos     = (returns > 0).sum()
    neg     = (returns < 0).sum()
    total   = pos + neg
    if total == 0:
        return 50.0
    dominance = abs(pos - neg) / total * 100
    return round(float(dominance), 2)


def _bb_width(series: pd.Series, period: int = 20) -> float:
    """Bollinger band genişliği (son değer)."""
    if len(series) < period:
        return 0.0
    roll  = series.rolling(period)
    mid   = roll.mean()
    std   = roll.std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    with_mid = mid.iloc[-1]
    if with_mid == 0:
        return 0.0
    return float((upper.iloc[-1] - lower.iloc[-1]) / with_mid * 100)


def _ema_slope(ema_series: pd.Series, lookback: int = 5) -> float:
    """EMA'nın son lookback mum içindeki yüzde değişimi."""
    if len(ema_series) < lookback + 1:
        return 0.0
    old = ema_series.iloc[-(lookback + 1)]
    new = ema_series.iloc[-1]
    if old == 0:
        return 0.0
    return float((new - old) / old * 100)


def _volume_change(df: pd.DataFrame, short: int = 5, long: int = 20) -> float:
    """Kısa vadeli hacim ortalamasının uzun vadeli ortalamaya oranı."""
    vols = df["volume"].values
    if len(vols) < long:
        return 1.0
    return float(vols[-short:].mean() / (vols[-long:].mean() or 1))


def _analyze_single(df15: pd.DataFrame, df1h: pd.DataFrame,
                    df4h: pd.DataFrame) -> dict[str, Any]:
    """Tek bir sembol için indikatör değerlerini hesapla."""
    def last(df: pd.DataFrame, col: str) -> float:
        return float(df[col].iloc[-2] if len(df) > 2 else df[col].iloc[-1])

    # 15m indikatörler
    ema20_15  = _ema(df15["close"], 20)
    ema50_15  = _ema(df15["close"], 50)
    atr15     = _atr(df15)
    rsi15     = _rsi(df15["close"], 14)

    # 1h indikatörler
    ema20_1h  = _ema(df1h["close"], 20)
    ema50_1h  = _ema(df1h["close"], 50)
    ema200_1h = _ema(df1h["close"], 100)  # limit=100 olduğu için 100 kullan
    rsi1h     = _rsi(df1h["close"], 14)
    slope_1h  = _ema_slope(ema50_1h)

    # 4h indikatörler
    ema20_4h  = _ema(df4h["close"], 20)
    ema50_4h  = _ema(df4h["close"], 50)
    slope_4h  = _ema_slope(ema50_4h)

    close15   = last(df15, "close")
    atr_pct   = float(atr15.iloc[-2]) / close15 * 100 if close15 > 0 else 0
    bb_w      = _bb_width(df15["close"])
    adx_val   = _adx_like(df1h)
    vol_chg   = _volume_change(df15)

    return {
        "close":        close15,
        "rsi_15m":      float(rsi15.iloc[-2]) if len(rsi15) > 2 else 50.0,
        "rsi_1h":       float(rsi1h.iloc[-2]) if len(rsi1h) > 2 else 50.0,
        "ema20_gt_50_15m":  float(ema20_15.iloc[-2]) > float(ema50_15.iloc[-2]),
        "ema20_gt_50_1h":   float(ema20_1h.iloc[-2]) > float(ema50_1h.iloc[-2]),
        "ema20_gt_50_4h":   float(ema20_4h.iloc[-2]) > float(ema50_4h.iloc[-2]),
        "close_gt_ema50_1h": close15 > float(ema50_1h.iloc[-2]),
        "close_gt_ema200_1h": close15 > float(ema200_1h.iloc[-2]),
        "slope_1h":     slope_1h,
        "slope_4h":     slope_4h,
        "atr_pct":      atr_pct,
        "bb_width":     bb_w,
        "adx":          adx_val,
        "vol_change":   vol_chg,
    }


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _classify_regime(ind: dict[str, Any]) -> tuple[str, float, str, str, str]:
    """
    indikatörlerden rejim belirle.
    Döndürür: (regime, confidence, direction, volatility, reason)
    """
    # Veri kalite kontrolü
    if ind["atr_pct"] <= 0 or math.isnan(ind["rsi_15m"]):
        return REGIME_INSUFFICIENT, 0, "Belirsiz", "Bilinmiyor", "Geçerli indikatör yok."

    # Yön oylaması (15m, 1h, 4h)
    bullish_votes = 0
    bearish_votes = 0

    if ind["ema20_gt_50_15m"]:   bullish_votes += 1
    else:                         bearish_votes += 1
    if ind["ema20_gt_50_1h"]:    bullish_votes += 2  # 1h daha ağırlıklı
    else:                         bearish_votes += 2
    if ind["ema20_gt_50_4h"]:    bullish_votes += 2
    else:                         bearish_votes += 2
    if ind["close_gt_ema50_1h"]: bullish_votes += 1
    else:                         bearish_votes += 1
    if ind["slope_1h"] > 0.1:    bullish_votes += 1
    elif ind["slope_1h"] < -0.1: bearish_votes += 1
    if ind["slope_4h"] > 0.1:    bullish_votes += 1
    elif ind["slope_4h"] < -0.1: bearish_votes += 1

    total_votes = bullish_votes + bearish_votes
    direction_score = (bullish_votes - bearish_votes) / max(total_votes, 1)

    if direction_score >= 0.1:
        direction = "Yukarı"
    elif direction_score <= -0.1:
        direction = "Aşağı"
    else:
        direction = "Yatay"

    # Volatilite değerlendirmesi
    atr_pct = ind["atr_pct"]
    if atr_pct > 3.0:
        volatility = "Yüksek"
    elif atr_pct < 0.8:
        volatility = "Düşük"
    else:
        volatility = "Normal"

    trend_strength = ind["adx"]

    # Rejim kararı
    abs_dir = abs(direction_score)
    regime: str
    reasons: list[str] = []

    if volatility == "Yüksek" and abs_dir < 0.4:
        regime = REGIME_HIGH_VOL
        reasons.append(f"Yüksek ATR %{atr_pct:.2f}")
    elif volatility == "Düşük" and trend_strength < 30:
        regime = REGIME_LOW_VOL
        reasons.append(f"Düşük ATR %{atr_pct:.2f}")
    elif direction == "Yatay" or (abs_dir < 0.2 and trend_strength < 35):
        regime = REGIME_SIDEWAYS
        reasons.append("EMA'lar yakın; yön belirsiz.")
    elif direction == "Yukarı":
        if trend_strength >= 55 and direction_score >= 0.5:
            regime = REGIME_STRONG_UP
            reasons.append(f"Güçlü yükseliş trendi (ADX={trend_strength:.0f}).")
        else:
            regime = REGIME_WEAK_UP
            reasons.append(f"Zayıf yükseliş (ADX={trend_strength:.0f}).")
    elif direction == "Aşağı":
        if trend_strength >= 55 and direction_score <= -0.5:
            regime = REGIME_STRONG_DOWN
            reasons.append(f"Güçlü düşüş trendi (ADX={trend_strength:.0f}).")
        else:
            regime = REGIME_WEAK_DOWN
            reasons.append(f"Zayıf düşüş (ADX={trend_strength:.0f}).")
    else:
        regime = REGIME_UNCLEAR
        reasons.append("Çelişkili sinyaller.")

    # Güven puanı
    base_conf  = min(100, abs_dir * 80 + trend_strength * 0.4)
    vol_penalt = 20 if volatility == "Yüksek" else 0
    conf       = max(0, min(100, base_conf - vol_penalt))

    # RSI aşırı bölgeler güven düşürür
    rsi = ind["rsi_1h"]
    if rsi > 75 or rsi < 25:
        conf = max(0, conf - 10)
        reasons.append(f"RSI aşırı bölgede ({rsi:.0f}).")

    # Hacim doğrulaması
    if ind["vol_change"] < 0.7:
        conf = max(0, conf - 10)
        reasons.append("Hacim düşük.")
    elif ind["vol_change"] > 1.5:
        reasons.append("Hacim artışı olumlu.")

    reason = " ".join(reasons) or "Standart koşullar."
    return regime, round(conf, 1), direction, volatility, reason


def detect_regime(symbol: str) -> dict[str, Any]:
    """
    Tek bir sembol için piyasa rejimini tespit et.
    Üç zaman dilimi (15m, 1h, 4h) kullanır.
    """
    df15 = _fetch_klines(symbol, "15m", 100)
    df1h = _fetch_klines(symbol, "1h",  100)
    df4h = _fetch_klines(symbol, "4h",  100)

    if df15 is None or df1h is None or df4h is None:
        return {
            "symbol": symbol, "regime": REGIME_INSUFFICIENT,
            "confidence": 0, "direction": "Belirsiz", "volatility": "Bilinmiyor",
            "trend_strength": 0, "atr_pct": 0, "suitable": False,
            "reason": "Veri alınamadı.", "ts": datetime.now(timezone.utc).isoformat(),
        }

    try:
        ind = _analyze_single(df15, df1h, df4h)
    except Exception as exc:
        return {
            "symbol": symbol, "regime": REGIME_INSUFFICIENT,
            "confidence": 0, "direction": "Belirsiz", "volatility": "Bilinmiyor",
            "trend_strength": 0, "atr_pct": 0, "suitable": False,
            "reason": f"İndikatör hatası: {exc}",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    regime, confidence, direction, volatility, reason = _classify_regime(ind)
    suitable = regime in SUITABLE_REGIMES and confidence >= 65

    return {
        "symbol":         symbol,
        "regime":         regime,
        "confidence":     confidence,
        "direction":      direction,
        "volatility":     volatility,
        "trend_strength": ind["adx"],
        "atr_pct":        round(ind["atr_pct"], 3),
        "bb_width":       round(ind["bb_width"], 3),
        "rsi_15m":        round(ind["rsi_15m"], 1),
        "rsi_1h":         round(ind["rsi_1h"], 1),
        "slope_1h":       round(ind["slope_1h"], 4),
        "vol_change":     round(ind["vol_change"], 3),
        "suitable":       suitable,
        "reason":         reason,
        "ts":             datetime.now(timezone.utc).isoformat(),
    }


def detect_market_regime(symbols: list[str], btc_symbol: str = "BTCUSDT") -> dict[str, Any]:
    """
    Genel piyasa rejimini belirle.
    BTC rejimi ana gösterge; diğer semboller oy hakkı verir.
    """
    # Önce BTC
    btc_regime = detect_regime(btc_symbol)

    # Diğer semboller (en fazla 3 örnek)
    sample = [s for s in symbols if s != btc_symbol][:3]
    others = []
    for sym in sample:
        try:
            r = detect_regime(sym)
            others.append(r)
        except Exception:
            pass

    # Genel yön oyu
    up_votes   = sum(1 for r in [btc_regime] + others if r["direction"] == "Yukarı")
    down_votes = sum(1 for r in [btc_regime] + others if r["direction"] == "Aşağı")
    total      = len([btc_regime] + others)

    overall_direction = "Yukarı" if up_votes > down_votes else (
                        "Aşağı"  if down_votes > up_votes  else "Yatay")

    # BTC rejimi en ağırlıklı
    market_regime    = btc_regime["regime"]
    market_conf      = btc_regime["confidence"]
    market_suitable  = btc_regime["suitable"] and market_conf >= 65

    return {
        "regime":       market_regime,
        "confidence":   market_conf,
        "direction":    btc_regime["direction"],
        "overall_direction": overall_direction,
        "volatility":   btc_regime["volatility"],
        "trend_strength": btc_regime["trend_strength"],
        "suitable":     market_suitable,
        "reason":       btc_regime["reason"],
        "btc":          btc_regime,
        "others":       others,
        "ts":           datetime.now(timezone.utc).isoformat(),
    }


def regime_score(regime_info: dict[str, Any]) -> float:
    """
    Rejim bilgisinden karar motoru için 0-100 arası skor üret.
    Uygun rejim + yüksek güven = yüksek skor.
    """
    regime     = regime_info.get("regime", REGIME_UNCLEAR)
    confidence = float(regime_info.get("confidence", 0))

    if regime == REGIME_INSUFFICIENT:
        return 0.0

    base = {
        REGIME_STRONG_UP:   90,
        REGIME_STRONG_DOWN: 90,
        REGIME_WEAK_UP:     70,
        REGIME_WEAK_DOWN:   70,
        REGIME_SIDEWAYS:    30,
        REGIME_HIGH_VOL:    25,
        REGIME_LOW_VOL:     45,
        REGIME_UNCLEAR:     20,
    }.get(regime, 20)

    return round(base * confidence / 100, 1)
