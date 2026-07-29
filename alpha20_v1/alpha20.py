from __future__ import annotations

import argparse
import json
import logging
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
TRADE_HISTORY_PATH = ROOT / "trade_history.json"
LOG_PATH = ROOT / "alpha20.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("alpha20")


@dataclass
class Position:
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    quantity: float
    risk_usdt: float
    opened_at: str


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)


def initial_state(config: dict[str, Any]) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "balance": float(config["starting_balance_usdt"]),
        "day": today,
        "day_start_balance": float(config["starting_balance_usdt"]),
        "consecutive_losses": 0,
        "position": None,
        "trades": [],
    }


MAX_SYMBOLS = 3
MIN_RISK_PCT = 0.25
MAX_RISK_PCT = 0.50
FEE_RATE = 0.001  # taraf başına %0.1 tahmini ücret


def validate_startup_config(config: dict[str, Any]) -> None:
    """Başlangıç kuralları — herhangi biri ihlal edilirse SystemExit."""
    errors: list[str] = []
    if config.get("mode") != "PAPER":
        errors.append("mode PAPER olmalı — gerçek işlem desteklenmiyor.")
    symbols = config.get("symbols") or []
    if not symbols:
        errors.append("En az 1 sembol gerekli.")
    if len(symbols) > MAX_SYMBOLS:
        errors.append(f"En fazla {MAX_SYMBOLS} sembol kullanılabilir (şu an {len(symbols)}).")
    risk = float(config.get("risk_per_trade_pct", 0))
    if not (MIN_RISK_PCT <= risk <= MAX_RISK_PCT):
        errors.append(
            f"risk_per_trade_pct {MIN_RISK_PCT}–{MAX_RISK_PCT} aralığında olmalı (şu an {risk})."
        )
    if int(config.get("max_open_positions", 0)) != 1:
        errors.append("max_open_positions 1 olmalı.")
    if int(config.get("leverage", 1)) != 1:
        errors.append("Kaldıraç desteklenmiyor — leverage 1 olmalı.")
    if errors:
        for err in errors:
            log.error("CONFIG HATASI: %s", err)
        raise SystemExit("Başlangıç doğrulaması başarısız: " + " | ".join(errors))


def print_startup_report(config: dict[str, Any], state: dict[str, Any]) -> None:
    print("Paper Trading Start Ready")
    print(f"Mode: {config['mode']}")
    print(f"Symbols: {', '.join(config['symbols'])}")
    print(f"Starting Virtual Balance: {config['starting_balance_usdt']:.2f} USDT")
    print(f"Current Balance: {state['balance']:.2f} USDT")
    print(f"Risk Per Trade: {config['risk_per_trade_pct']}%")
    print(f"Max Open Positions: {config['max_open_positions']}")
    print(f"Leverage: 1x")
    print(f"Stop Loss: ATR x {config['atr_stop_multiplier']} (zorunlu)")
    print(f"Take Profit: stop mesafesi x {config['reward_risk_ratio']} (zorunlu)")


def compute_realized_pnl(
    entry_price: float,
    exit_price: float,
    quantity: float,
    side: str,
    fee_rate: float = FEE_RATE,
) -> dict[str, float]:
    """Tek gerçek kaynak: realized PnL hesabı (BUG-001).

    Console, State, Trade History ve tüm raporlar bu fonksiyonun çıktısını kullanır.
    Dönüş: {"gross_pnl", "fee_usdt", "pnl"} — pnl = gross_pnl - fee_usdt.
    """
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"Geçersiz yön: {side}")
    if entry_price <= 0 or exit_price <= 0 or quantity <= 0:
        raise ValueError("entry_price, exit_price ve quantity pozitif olmalı.")
    direction = 1 if side == "LONG" else -1
    gross_pnl = (exit_price - entry_price) * quantity * direction
    fee_usdt = (entry_price + exit_price) * quantity * fee_rate  # giriş + çıkış
    return {
        "gross_pnl": round(gross_pnl, 8),
        "fee_usdt": round(fee_usdt, 8),
        "pnl": round(gross_pnl - fee_usdt, 8),
    }


def append_trade_history(trade: dict[str, Any], path: Path | None = None) -> None:
    """Kapanan her işlemi trade_history.json dosyasına otomatik ekler (atomik)."""
    if path is None:
        path = TRADE_HISTORY_PATH  # çağrı anında çözülür (test izolasyonu için)
    history = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except (json.JSONDecodeError, OSError):
            log.warning("trade_history.json okunamadı — yeni liste başlatılıyor.")
            history = []
    history.append(trade)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    temp.replace(path)


def print_performance_summary(state: dict[str, Any]) -> None:
    """Terminal performans özeti."""
    trades = state.get("trades", [])
    print("\n══════ PAPER PERFORMANS ÖZETİ ══════")
    print(f"Bakiye: {state['balance']:.2f} USDT")
    if not trades:
        print("Kapanan işlem yok.")
        print("════════════════════════════════════")
        return
    pnls = [float(t.get("pnl", 0) or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    fees = sum(float(t.get("fee_usdt", 0) or 0) for t in trades)
    best = max(trades, key=lambda t: float(t.get("pnl", 0) or 0))
    worst = min(trades, key=lambda t: float(t.get("pnl", 0) or 0))
    print(f"Toplam işlem: {len(trades)} | Kazanç: {len(wins)} | Zarar: {len(losses)}")
    print(f"Kazanma oranı: {len(wins) / len(trades) * 100:.1f}%")
    print(f"Net PnL: {sum(pnls):+.2f} USDT | Toplam fee: {fees:.2f} USDT")
    if wins:
        print(f"Ortalama kazanç: {sum(wins)/len(wins):+.2f} USDT")
    if losses:
        print(f"Ortalama zarar: {sum(losses)/len(losses):+.2f} USDT")
    print(f"En iyi: {best.get('symbol')} {float(best.get('pnl', 0)):+.2f} | "
          f"En kötü: {worst.get('symbol')} {float(worst.get('pnl', 0)):+.2f}")
    print("════════════════════════════════════")


def reset_day_if_needed(state: dict[str, Any]) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if state["day"] != today:
        state["day"] = today
        state["day_start_balance"] = state["balance"]


def fetch_klines_safe(
    symbol: str, interval: str, limit: int = 300, state: dict[str, Any] | None = None
) -> pd.DataFrame | None:
    """Ağ/veri hatasında None döndürür — yeni pozisyon açılmasını engeller."""
    try:
        df = fetch_klines(symbol, interval, limit)
        if df is None or df.empty:
            raise ValueError("Boş kline verisi.")
        return df
    except Exception as exc:
        if state is not None:
            state["network_errors"] = int(state.get("network_errors", 0)) + 1
        log.warning("VERİ HATASI | %s %s | %s — yeni işlem açılmayacak.", symbol, interval, exc)
        return None


def diagnose_ssl_error(exc: Exception) -> str:
    """SSL hatasının olası kök nedenini Türkçe olarak ayırt eder.

    Not: SSL doğrulaması ASLA kapatılmaz (verify parametresini kapatmak yasak). Bu fonksiyon
    yalnızca operatöre doğru çözüm adımını göstermek içindir.
    """
    text = str(exc).lower()
    if "certificate has expired" in text or "not yet valid" in text:
        return ("SSL hatası (saat/tarih şüphesi): sertifika tarih doğrulaması "
                "başarısız. Windows sistem saatinin ve tarihinin doğru olduğunu "
                "kontrol edin; saat sapması SSL doğrulamasını bozar.")
    if "self signed certificate" in text or "self-signed certificate" in text:
        return ("SSL hatası (proxy/antivirüs şüphesi): sertifika zincirinde "
                "kendinden imzalı sertifika görüldü. Kurumsal proxy veya "
                "antivirüs SSL trafiğine araya giriyor olabilir; Binance alan "
                "adları için SSL denetimini (HTTPS tarama) devre dışı bırakın.")
    if "unable to get local issuer certificate" in text or \
            "certificate verify failed" in text:
        return ("SSL hatası (sertifika paketi şüphesi): yerel CA sertifikası "
                "doğrulanamadı. certifi paketi eski olabilir — "
                "INSTALL_WINDOWS.cmd dosyasını yeniden çalıştırın "
                "(certifi otomatik güncellenir). Sorun sürerse proxy/antivirüs "
                "SSL araya girmesini de kontrol edin.")
    return ("SSL hatası (neden belirsiz): certifi güncellemesi için "
            "INSTALL_WINDOWS.cmd'yi yeniden çalıştırın; ayrıca sistem saatini "
            "ve proxy/antivirüs SSL denetimini kontrol edin. "
            f"Teknik ayrıntı: {exc}")


def diagnose_network_error(exc: Exception) -> str:
    """Ağ hatasının (DNS, zaman aşımı, bağlantı reddi...) olası kök nedenini
    Türkçe olarak ayırt eder.

    SSL hataları için diagnose_ssl_error kullanılır; bu fonksiyon diğer
    requests ağ hatalarını (ConnectionError, Timeout) kapsar.
    """
    text = str(exc).lower()
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in text or \
            "timeout" in text:
        return ("Ağ hatası (zaman aşımı şüphesi): Binance sunucusu zamanında "
                "yanıt vermedi. İnternet bağlantınız yavaş olabilir veya "
                "güvenlik duvarı/proxy trafiği geciktiriyor olabilir. "
                "Bağlantınızı test edin; sorun sürerse birkaç dakika sonra "
                "kendiliğinden düzelebilir (geçici Binance yavaşlaması).")
    if "getaddrinfo failed" in text or "name or service not known" in text or \
            "nodename nor servname" in text or "temporary failure in name resolution" in text or \
            "failed to resolve" in text or "name resolution" in text:
        return ("Ağ hatası (DNS şüphesi): Binance alan adı çözümlenemedi. "
                "İnternet bağlantınızı kontrol edin; sorun sürerse Windows DNS "
                "ayarlarını (örn. 8.8.8.8) değiştirin veya modemi yeniden "
                "başlatın. VPN/proxy kullanıyorsanız kapatıp deneyin.")
    if "connection refused" in text or "actively refused" in text:
        return ("Ağ hatası (güvenlik duvarı/engelleme şüphesi): bağlantı "
                "reddedildi. Güvenlik duvarı, antivirüs veya kurumsal proxy "
                "Binance bağlantısını engelliyor olabilir; Windows Güvenlik "
                "Duvarı ve antivirüs ayarlarında Python/uygulamaya izin verin.")
    if "connection reset" in text or "connection aborted" in text or \
            "remote end closed" in text:
        return ("Ağ hatası (bağlantı koptu): bağlantı karşı tarafça "
                "sıfırlandı. Geçici ağ kesintisi veya proxy/antivirüs araya "
                "girmesi olabilir; birkaç dakika sonra tekrar denenir, sorun "
                "sürerse ağ ekipmanınızı (modem/router) yeniden başlatın.")
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ("Ağ hatası (bağlantı kurulamadı): Binance sunucusuna "
                "ulaşılamıyor. İnternet bağlantınızı, güvenlik duvarını ve "
                "varsa VPN/proxy ayarlarını kontrol edin. "
                f"Teknik ayrıntı: {exc}")
    return ("Ağ hatası (neden belirsiz): internet bağlantınızı ve güvenlik "
            "duvarı/proxy ayarlarını kontrol edin. "
            f"Teknik ayrıntı: {exc}")


# ── Rate-limit geri çekilme (429/418) ────────────────────────────────────────
# Binance 429 (çok fazla istek) gelince tempo otomatik düşürülür; istekler
# sürerse Binance IP yasağı (418) uygular. Bu paylaşılan durum, tüm kline
# çağrılarının (alpha20 + market_regime) geri çekilme süresi boyunca yeni
# istek atmasını engeller.
RATE_LIMIT_DEFAULT_BACKOFF = 60.0     # 429: Retry-After yoksa ilk bekleme (sn)
RATE_LIMIT_MAX_BACKOFF = 900.0        # 429: artan bekleme üst sınırı (sn)
RATE_LIMIT_BAN_BACKOFF = 300.0        # 418: Retry-After yoksa varsayılan (sn)

_rate_limit_state: dict[str, Any] = {
    "blocked_until": 0.0,      # time.time() — bu ana kadar yeni istek yok
    "reason": "",              # operatöre gösterilecek Türkçe açıklama
    "consecutive_429": 0,      # artan bekleme için ardışık 429 sayacı
}

# Panelin (ayrı süreç — gunicorn) geri çekilmeyi görebilmesi için durum
# geçişlerinde diske yazılır. Yalnız geçişlerde yazılır (her istek değil).
RATE_LIMIT_STATE_PATH = ROOT / "rate_limit_state.json"


def _persist_rate_limit_state() -> None:
    """Geri çekilme durumunu panel için diske yazar (atomik).

    Yazma hatası taramayı durdurmaz; yalnız log'a düşer (görünürlük
    yardımcı özelliktir, ticaret akışını asla bloklamamalı)."""
    payload = {
        "blocked_until": float(_rate_limit_state["blocked_until"]),
        "reason": str(_rate_limit_state["reason"]),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        temp = RATE_LIMIT_STATE_PATH.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        temp.replace(RATE_LIMIT_STATE_PATH)
    except OSError as exc:
        log.warning("GERİ ÇEKİLME | durum dosyası yazılamadı: %s", exc)


def read_rate_limit_file(now: float | None = None,
                         path: Path | None = None) -> dict[str, Any]:
    """Diskteki geri çekilme durumunu okur (panel süreci için).

    Dönüş: {"remaining_seconds": float, "reason": str}. Dosya yoksa,
    bozuksa veya süre dolduysa remaining_seconds 0 olur."""
    if path is None:
        path = RATE_LIMIT_STATE_PATH
    if now is None:
        now = time.time()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        blocked_until = float(data.get("blocked_until", 0.0))
        reason = str(data.get("reason", ""))
    except (OSError, ValueError, TypeError):
        return {"remaining_seconds": 0.0, "reason": ""}
    remaining = max(0.0, blocked_until - now)
    if remaining <= 0:
        return {"remaining_seconds": 0.0, "reason": ""}
    return {"remaining_seconds": remaining, "reason": reason}


def reset_rate_limit_state() -> None:
    """Geri çekilme durumunu sıfırlar (test ve manuel kurtarma için)."""
    _rate_limit_state["blocked_until"] = 0.0
    _rate_limit_state["reason"] = ""
    _rate_limit_state["consecutive_429"] = 0
    _persist_rate_limit_state()


def rate_limit_remaining(now: float | None = None) -> float:
    """Geri çekilme bitimine kalan saniye (0 → istek atılabilir)."""
    if now is None:
        now = time.time()
    return max(0.0, float(_rate_limit_state["blocked_until"]) - now)


def rate_limit_reason() -> str:
    return str(_rate_limit_state.get("reason", ""))


def _parse_retry_after(response: Any) -> float | None:
    """Retry-After başlığını saniye olarak okur; okunamazsa None."""
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def register_rate_limit(status: int, response: Any = None,
                        now: float | None = None) -> float:
    """429/418 yanıtını kaydeder ve geri çekilme süresini (sn) döndürür.

    - 429: Retry-After varsa o kadar; yoksa artan bekleme
      (60s → 120s → 240s ... en fazla 900s).
    - 418: Retry-After varsa o kadar; yoksa 300s. Yasak süresi boyunca
      yeni istek atılmaz.
    """
    if now is None:
        now = time.time()
    retry_after = _parse_retry_after(response)
    if status == 418:
        wait = retry_after if retry_after is not None else RATE_LIMIT_BAN_BACKOFF
        _rate_limit_state["consecutive_429"] = 0
        reason = (f"Binance IP yasağı (418): IP'niz geçici olarak engellendi. "
                  f"Yeni istekler {wait:.0f} saniye boyunca durduruldu; yasak "
                  "süresi dolunca tarama kendiliğinden devam eder. Aynı IP'den "
                  "yoğun istek atan diğer uygulamaları kapatın.")
    elif status == 429:
        count = int(_rate_limit_state["consecutive_429"]) + 1
        _rate_limit_state["consecutive_429"] = count
        if retry_after is not None:
            wait = retry_after
        else:
            wait = min(RATE_LIMIT_MAX_BACKOFF,
                       RATE_LIMIT_DEFAULT_BACKOFF * (2 ** (count - 1)))
        reason = (f"Binance istek limiti (429 — çok fazla istek): istek temposu otomatik "
                  f"düşürüldü — tarama {wait:.0f} saniye duraklatıldı "
                  "(IP yasağına dönüşmemesi için). Bekleme bitince "
                  "kendiliğinden devam eder.")
    else:
        return 0.0
    blocked_until = now + wait
    if blocked_until > float(_rate_limit_state["blocked_until"]):
        _rate_limit_state["blocked_until"] = blocked_until
        _rate_limit_state["reason"] = reason
        _persist_rate_limit_state()
    log.warning("GERİ ÇEKİLME | %s", reason)
    return wait


def note_rate_limit_success() -> None:
    """Başarılı istek sonrası ardışık 429 sayacını sıfırlar."""
    _rate_limit_state["consecutive_429"] = 0


def diagnose_http_error(exc: Exception) -> str:
    """HTTP durum hatasının (429/418/5xx...) olası nedenini Türkçe açıklar.

    Binance rate-limit ve sunucu hataları operatöre anlaşılır kılavuz
    mesajla gösterilir; ham İngilizce HTTPError metni yerine kullanılır.
    """
    status: int | None = None
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            status = int(response.status_code)
        except (TypeError, ValueError):
            status = None
    if status == 429:
        return ("Binance hatası (429 — çok fazla istek): istek sıklığı limiti "
                "aşıldı. Bot bir süre bekleyip otomatik yeniden dener; başka "
                "botlar/uygulamalar aynı IP'den Binance'e istek atıyorsa "
                "kapatın. Kalıcıysa istek sıklığını (tarama aralığını) düşürün.")
    if status == 418:
        return ("Binance hatası (418 — IP geçici olarak yasaklandı): 429 "
                "uyarılarına rağmen istekler sürdüğü için IP'niz Binance "
                "tarafından geçici olarak engellendi. Botu bir süre durdurun; "
                "yasak süresi dolunca kendiliğinden açılır. Aynı IP'den "
                "yoğun istek atan diğer uygulamaları kapatın.")
    if status is not None and 500 <= status < 600:
        return (f"Binance hatası ({status} — sunucu tarafı geçici sorun): "
                "sorun sizin bağlantınızda değil, Binance sunucularında. "
                "Genellikle birkaç dakika içinde kendiliğinden düzelir; "
                "bot otomatik yeniden dener, bir işlem yapmanız gerekmez.")
    if status is not None:
        return (f"Binance hatası (HTTP {status}): beklenmeyen durum kodu. "
                "Sorun sürerse Binance durum sayfasını kontrol edin. "
                f"Teknik ayrıntı: {exc}")
    return ("Binance hatası (HTTP durum kodu okunamadı): beklenmeyen yanıt. "
            f"Teknik ayrıntı: {exc}")


def fetch_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    remaining = rate_limit_remaining()
    if remaining > 0:
        detail = (f"Geri çekilme aktif — yeni istek atılmadı ({remaining:.0f} "
                  f"saniye kaldı). {rate_limit_reason()}")
        log.warning("GERİ ÇEKİLME | %s %s | %s", symbol, interval, detail)
        raise RuntimeError(detail)
    try:
        response = requests.get(
            f"{BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15,
        )
    except requests.exceptions.SSLError as exc:
        detail = diagnose_ssl_error(exc)
        log.warning("SSL DOĞRULAMA HATASI | %s %s | %s", symbol, interval, detail)
        raise RuntimeError(detail) from exc
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        detail = diagnose_network_error(exc)
        log.warning("AĞ HATASI | %s %s | %s", symbol, interval, detail)
        raise RuntimeError(detail) from exc
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (429, 418):
            register_rate_limit(int(status), getattr(exc, "response", None))
        detail = diagnose_http_error(exc)
        log.warning("HTTP HATASI | %s %s | %s", symbol, interval, detail)
        raise RuntimeError(detail) from exc
    note_rate_limit_success()
    rows = response.json()
    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base",
        "taker_quote", "ignore",
    ]
    df = pd.DataFrame(rows, columns=columns)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]

    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ema200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    out["rsi14"] = 100 - (100 / (1 + rs))

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = true_range.ewm(alpha=1/14, adjust=False).mean()
    out["volume_ma20"] = out["volume"].rolling(20).mean()
    return out


def score_setup(fast: pd.DataFrame, trend: pd.DataFrame) -> tuple[str | None, int, dict[str, Any]]:
    f = fast.iloc[-2]   # Yalnızca kapanmış mum
    t = trend.iloc[-2]

    long_score = 0
    short_score = 0

    # 1 saatlik ana trend: 30 puan
    if t["ema50"] > t["ema200"] and t["close"] > t["ema50"]:
        long_score += 30
    if t["ema50"] < t["ema200"] and t["close"] < t["ema50"]:
        short_score += 30

    # 15 dakikalık kısa trend: 20 puan
    if f["ema20"] > f["ema50"] and f["close"] > f["ema20"]:
        long_score += 20
    if f["ema20"] < f["ema50"] and f["close"] < f["ema20"]:
        short_score += 20

    # RSI: 20 puan
    if 52 <= f["rsi14"] <= 68:
        long_score += 20
    if 32 <= f["rsi14"] <= 48:
        short_score += 20

    # Hacim: 15 puan
    volume_ratio = f["volume"] / f["volume_ma20"] if f["volume_ma20"] else 0
    if volume_ratio >= 1.10:
        long_score += 15
        short_score += 15

    # Son mum yönü: 15 puan
    if f["close"] > f["open"]:
        long_score += 15
    if f["close"] < f["open"]:
        short_score += 15

    details = {
        "price": float(f["close"]),
        "atr": float(f["atr14"]),
        "rsi": round(float(f["rsi14"]), 2),
        "volume_ratio": round(float(volume_ratio), 2),
        "long_score": long_score,
        "short_score": short_score,
    }

    if long_score > short_score:
        return "LONG", long_score, details
    if short_score > long_score:
        return "SHORT", short_score, details
    return None, max(long_score, short_score), details


class TradeSkippedError(Exception):
    """Ekonomik filtre: işlem açılmadı (risk ihlali DEĞİL)."""


def evaluate_trade_economics(
    entry: float,
    atr: float,
    side: str,
    balance: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Mission 11 — pre-trade ekonomik doğrulama (salt hesap, durum değiştirmez).

    Kural: expected_gross_profit <= expected_total_fee * safety_factor → SKIP.
    """
    safety_factor = float(config.get("fee_safety_factor", 2.0))
    stop_distance = atr * config["atr_stop_multiplier"]
    rr = config["reward_risk_ratio"]
    risk_usdt = balance * config["risk_per_trade_pct"] / 100
    quantity = risk_usdt / stop_distance if stop_distance > 0 else 0.0
    if side == "LONG":
        stop = entry - stop_distance
        target = entry + stop_distance * rr
    else:
        stop = entry + stop_distance
        target = entry - stop_distance * rr
    expected_gross_profit = risk_usdt * rr  # hedefe ulaşırsa brüt kâr
    expected_total_fee = (entry + target) * quantity * FEE_RATE
    fee_gross_ratio = (expected_total_fee / expected_gross_profit
                       if expected_gross_profit > 0 else float("inf"))
    skip = expected_gross_profit <= expected_total_fee * safety_factor
    return {
        "entry": entry,
        "stop": stop,
        "atr": atr,
        "stop_distance": stop_distance,
        "stop_distance_pct": round(stop_distance / entry * 100, 6) if entry else None,
        "position_size": quantity,
        "expected_gross_profit": round(expected_gross_profit, 8),
        "expected_total_fee": round(expected_total_fee, 8),
        "risk_reward": rr,
        "fee_gross_ratio": round(fee_gross_ratio, 6),
        "safety_factor": safety_factor,
        "skip": skip,
    }


def can_open(
    config: dict[str, Any], state: dict[str, Any], symbol: str | None = None
) -> tuple[bool, str]:
    position = state.get("position")
    if position is not None:
        if symbol is not None and position.get("symbol") == symbol:
            return False, f"{symbol} için zaten açık pozisyon var (mükerrer engellendi)."
        return False, "Açık pozisyon var."
    if state["consecutive_losses"] >= config["max_consecutive_losses"]:
        return False, "Arka arkaya zarar limiti doldu."
    daily_loss = state["day_start_balance"] - state["balance"]
    daily_limit = state["day_start_balance"] * config["daily_loss_limit_pct"] / 100
    if daily_loss >= daily_limit:
        return False, "Günlük zarar limiti doldu."
    return True, "Uygun."


def open_paper_position(
    symbol: str,
    side: str,
    details: dict[str, Any],
    config: dict[str, Any],
    state: dict[str, Any],
) -> None:
    if state.get("position") is not None:
        raise ValueError("Zaten açık pozisyon var — ikinci pozisyon açılamaz.")
    entry = details["price"]
    atr = details["atr"]
    if entry is None or entry <= 0:
        raise ValueError(f"Geçersiz giriş fiyatı: {entry}")
    if atr is None or atr <= 0:
        raise ValueError(f"Geçersiz ATR: {atr}")
    stop_distance = atr * config["atr_stop_multiplier"]
    if stop_distance <= 0:
        raise ValueError("ATR stop mesafesi geçersiz.")

    risk_usdt = state["balance"] * config["risk_per_trade_pct"] / 100
    if risk_usdt <= 0 or state["balance"] <= 0:
        raise ValueError(f"Yetersiz sanal bakiye: {state['balance']:.2f} USDT")
    quantity = risk_usdt / stop_distance

    # Mission 11 — pre-trade ekonomik doğrulama (muhasebe/risk motoruna dokunmaz)
    econ = evaluate_trade_economics(entry, atr, side, state["balance"], config)
    if econ["skip"]:
        log.info(
            "SKIPPED: Expected fee exceeds acceptable threshold. | %s %s | "
            "beklenen brüt=%.2f beklenen fee=%.2f oran=%.2f sf=%.1f",
            symbol, side, econ["expected_gross_profit"],
            econ["expected_total_fee"], econ["fee_gross_ratio"], econ["safety_factor"],
        )
        raise TradeSkippedError(
            "SKIPPED: Expected fee exceeds acceptable threshold. "
            f"(gross={econ['expected_gross_profit']:.2f} "
            f"fee={econ['expected_total_fee']:.2f} sf={econ['safety_factor']})")

    if side == "LONG":
        stop = entry - stop_distance
        target = entry + stop_distance * config["reward_risk_ratio"]
    else:
        stop = entry + stop_distance
        target = entry - stop_distance * config["reward_risk_ratio"]

    position = Position(
        symbol=symbol,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        quantity=quantity,
        risk_usdt=risk_usdt,
        opened_at=datetime.now(timezone.utc).isoformat(),
    )
    state["position"] = asdict(position)
    log.info(
        "PAPER AÇILDI | %s %s | giriş=%.4f stop=%.4f hedef=%.4f risk=%.2f USDT",
        symbol, side, entry, stop, target, risk_usdt,
    )


def manage_position(state: dict[str, Any]) -> None:
    raw = state.get("position")
    if not raw:
        return

    # auto_controller genişletilmiş alanlar (regime, final_score, ...) yazar;
    # kanonik SL/TP kontrolü yalnız Position alanlarını kullanır.
    pos = Position(**{f: raw[f] for f in Position.__dataclass_fields__
                      if f in raw})
    df = fetch_klines_safe(pos.symbol, "1m", 5, state=state)
    if df is None:
        # Veri yok — pozisyon güvenle korunur, kapatma kararı verilmez.
        return
    last = df.iloc[-1]
    high, low = float(last["high"]), float(last["low"])

    exit_price = None
    result = None

    # Aynı mum içinde hem stop hem hedef görülürse temkinli olarak stop varsayılır.
    if pos.side == "LONG":
        if low <= pos.stop:
            exit_price, result = pos.stop, "LOSS"
        elif high >= pos.target:
            exit_price, result = pos.target, "WIN"
    else:
        if high >= pos.stop:
            exit_price, result = pos.stop, "LOSS"
        elif low <= pos.target:
            exit_price, result = pos.target, "WIN"

    if exit_price is None:
        return

    # BUG-001: PnL yalnızca compute_realized_pnl ile hesaplanır (tek kaynak).
    pnl_data = compute_realized_pnl(pos.entry, exit_price, pos.quantity, pos.side)
    pnl = pnl_data["pnl"]
    close_reason = "STOP_LOSS" if result == "LOSS" else "TAKE_PROFIT"
    state["balance"] += pnl
    state["consecutive_losses"] = state["consecutive_losses"] + 1 if pnl < 0 else 0
    trade_record = {
        **raw,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "entry_price": pos.entry,
        "exit_price": exit_price,
        "exit": exit_price,
        "fee_usdt": pnl_data["fee_usdt"],
        "gross_pnl": pnl_data["gross_pnl"],
        "pnl": pnl,
        "result": result,
        "close_reason": close_reason,
        "balance_after": round(state["balance"], 8),
    }
    state["trades"].append(trade_record)
    state["position"] = None  # Muhasebe önce kapanır — history yazımı best-effort.
    try:
        append_trade_history(trade_record)
    except OSError as exc:
        log.warning("trade_history.json yazılamadı (%s) — muhasebe etkilenmedi.", exc)
    log.info(
        "PAPER KAPANDI | %s %s | sonuç=%s pnl=%.2f bakiye=%.2f",
        pos.symbol, pos.side, result, pnl, state["balance"],
    )


def run_cycle(config: dict[str, Any], state: dict[str, Any]) -> None:
    reset_day_if_needed(state)
    manage_position(state)

    allowed, reason = can_open(config, state)
    if not allowed:
        log.info("Yeni işlem yok: %s", reason)
        save_json(STATE_PATH, state)
        return

    candidates = []
    data_error = False
    for symbol in config["symbols"]:
        sym_ok, sym_reason = can_open(config, state, symbol=symbol)
        if not sym_ok:
            log.info("%s atlandı: %s", symbol, sym_reason)
            continue
        try:
            fast_raw = fetch_klines_safe(symbol, config["interval"], state=state)
            trend_raw = fetch_klines_safe(symbol, config["trend_interval"], state=state)
            if fast_raw is None or trend_raw is None:
                data_error = True  # Herhangi bir veri hatası → bu döngüde hiç işlem açma.
                continue
            fast = add_indicators(fast_raw)
            trend = add_indicators(trend_raw)
            side, score, details = score_setup(fast, trend)
            log.info(
                "%s | yön=%s skor=%s RSI=%s hacim=%.2f",
                symbol, side, score, details["rsi"], details["volume_ratio"],
            )
            if side and score >= config["minimum_score"]:
                candidates.append((score, symbol, side, details))
        except Exception as exc:
            log.exception("%s taranamadı: %s", symbol, exc)

    if data_error:
        log.warning("Ağ/veri hatası nedeniyle bu döngüde yeni pozisyon açılmayacak.")
    elif candidates:
        score, symbol, side, details = max(candidates, key=lambda x: x[0])
        try:
            open_paper_position(symbol, side, details, config, state)
        except TradeSkippedError as exc:
            state["skipped_trades"] = state.get("skipped_trades", 0) + 1
            log.info("%s %s açılmadı — %s (ekonomik filtre, risk ihlali değil)",
                     symbol, side, exc)
    else:
        log.info("Eşik üzerinde fırsat bulunmadı.")

    save_json(STATE_PATH, state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha-20 v1 PAPER trading bot")
    parser.add_argument("--once", action="store_true", help="Bir kez tara ve çık.")
    parser.add_argument("--reset", action="store_true", help="Sanal hesabı sıfırla.")
    parser.add_argument("--dry-run", action="store_true", help="Doğrula, raporla ve çık — işlem yok.")
    parser.add_argument("--report", action="store_true", help="Başlangıç raporunu yazdır.")
    parser.add_argument("--summary", action="store_true", help="Performans özetini yazdır ve çık.")
    parser.add_argument("--validate", action="store_true",
                        help="Doğrulama katmanını çalıştır (sağlık + metrikler + rapor) ve çık.")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    validate_startup_config(config)

    if args.reset or not STATE_PATH.exists():
        state = initial_state(config)
        save_json(STATE_PATH, state)
    else:
        state = load_json(STATE_PATH, initial_state(config))

    log.info("Alpha-20 v1 başladı | MOD=PAPER | bakiye=%.2f", state["balance"])
    log.info("PAPER modu doğrulandı — gerçek emir gönderimi yok, API anahtarı yok.")

    if args.validate:
        from validation import run_validation
        ok, _ = run_validation(state=state, config=config)
        raise SystemExit(0 if ok else 1)
    if args.summary:
        print_performance_summary(state)
        return
    if args.report or args.dry_run:
        print_startup_report(config, state)
    if args.dry_run:
        log.info("Dry-run tamamlandı — döngü başlatılmadı.")
        return

    time.sleep(2)  # Başlangıç onay gecikmesi

    if args.once:
        run_cycle(config, state)
        print_performance_summary(state)
        return

    while True:
        try:
            run_cycle(config, state)
        except KeyboardInterrupt:
            log.info("Kullanıcı tarafından durduruldu.")
            print_performance_summary(state)
            break
        except Exception as exc:
            log.exception("Döngü hatası: %s", exc)
        time.sleep(int(config["scan_seconds"]))


if __name__ == "__main__":
    main()
