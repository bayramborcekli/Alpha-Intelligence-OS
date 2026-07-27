"""Mission 1400.2 — Salt-okunur canlı pano servis katmanı.

Tüm borsa erişimi YALNIZCA sunucu tarafındadır ve açık allowlist'teki GET
istekleriyle sınırlıdır. Emir, transfer, çekim, kaldıraç/margin/pozisyon
modu değişikliği yapan HİÇBİR kod yolu yoktur ve yazma sayaçları her zaman
sıfırdır. Ham borsa yanıtları asla dışarı verilmez; yalnızca tipli,
sterilize modeller döner. Finansal değerler Decimal-uyumlu string olarak
taşınır (binary float hesap yok).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import requests

ROOT = Path(__file__).resolve().parent
LEDGER_PATH = ROOT / "alpha20_v1" / "mission1310b" / "ledger_events.json"
M1310B_REPORT = ROOT / "alpha20_v1" / "mission1310b" / "mission_1310b_report.json"

GLOBAL_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"
TR_BASE = "https://www.trbinance.com"

# ── Yazma güvenliği ──────────────────────────────────────────────────────────
# Bu sayaçlar hiçbir kod yolunda artırılmaz; sistem durumu raporlar.
WRITE_COUNTERS = {
    "order_endpoint_requests": 0,
    "transfer_endpoint_requests": 0,
    "withdrawal_endpoint_requests": 0,
    "other_exchange_write_requests": 0,
}

# Yalnızca GET; ağ isteğinden ÖNCE zorunlu.
GLOBAL_ALLOWLIST = {
    ("GET", "/fapi/v2/account"),
    ("GET", "/fapi/v2/balance"),
    ("GET", "/fapi/v2/positionRisk"),
    ("GET", "/fapi/v1/openOrders"),
    ("GET", "/fapi/v1/positionSide/dualSide"),
}
SPOT_ALLOWLIST = {
    ("GET", "/api/v3/account"),        # imzalı, salt-okunur hesap
    ("GET", "/api/v3/ticker/price"),   # imzasız, halka açık fiyat
}
TR_ALLOWLIST = {
    ("GET", "/open/v1/account/spot"),
}

# ── Önbellek ve tazelik politikası (merkezî) ────────────────────────────────
CACHE_TTL = {          # saniye — sunucu tarafı güvenli okuma önbelleği
    "global_spot": 15,
    "global_account": 15,
    "global_positions": 10,
    "global_orders": 10,
    "tr_account": 30,
    "tr_movements": 300,
}
FRESH_LIMIT = {        # saniye — bu yaşın üstü ESKİ VERİ
    "global_spot": 60,
    "global_account": 60,
    "global_positions": 60,
    "global_orders": 60,
    "tr_account": 60,
    "tr_movements": 900,
}
MAX_RETRIES = 2        # yalnızca güvenli GET; üstel geri çekilme
BACKOFF_BASE = 0.4

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_started_monotonic = time.monotonic()
_last_full_refresh: str | None = None


def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


def _dec(v: Any) -> str:
    """Decimal-uyumlu string; asla float'a çevrilmez."""
    try:
        return str(Decimal(str(v)))
    except (InvalidOperation, TypeError, ValueError):
        return "0"


def _dec_val(v: Any) -> Decimal:
    """Hata fırlatmayan güvenli Decimal ayrıştırıcı (bozuk alan → 0)."""
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SafeExchangeError(Exception):
    """Normalize edilmiş güvenli hata."""
    def __init__(self, code: str, message_tr: str):
        super().__init__(code)
        self.code = code
        self.message_tr = message_tr


ERROR_MESSAGES = {
    "EXCHANGE_AUTH_FAILED": "Borsa kimlik doğrulaması başarısız.",
    "EXCHANGE_UNAVAILABLE": "Borsaya şu anda ulaşılamıyor.",
    "EXCHANGE_RATE_LIMITED": "Borsa istek sınırı aşıldı; kısa süre sonra "
                             "tekrar denenecek.",
    "EXCHANGE_TIMEOUT": "Borsa yanıt vermedi (zaman aşımı).",
    "INVALID_EXCHANGE_RESPONSE": "Borsa beklenmeyen bir yanıt döndürdü.",
    "STALE_DATA": "Veri güncel değil.",
}


def _classify_http(status: int) -> SafeExchangeError:
    if status in (401, 403) or status == -2015:
        return SafeExchangeError("EXCHANGE_AUTH_FAILED",
                                 ERROR_MESSAGES["EXCHANGE_AUTH_FAILED"])
    if status in (418, 429):
        return SafeExchangeError("EXCHANGE_RATE_LIMITED",
                                 ERROR_MESSAGES["EXCHANGE_RATE_LIMITED"])
    if status >= 500:
        return SafeExchangeError("EXCHANGE_UNAVAILABLE",
                                 ERROR_MESSAGES["EXCHANGE_UNAVAILABLE"])
    return SafeExchangeError("INVALID_EXCHANGE_RESPONSE",
                             ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])


def _signed_get(base: str, path: str, allowlist: set, key: str, secret: str,
                params: dict | None = None, timeout: int = 10) -> Any:
    """Allowlist'li, imzalı, yalnızca-GET istek; sınırlı yeniden deneme."""
    if ("GET", path) not in allowlist:
        raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
    last_err: SafeExchangeError | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            p = dict(params or {})
            p["timestamp"] = int(time.time() * 1000)
            qs = urllib.parse.urlencode(p)
            p["signature"] = hmac.new(secret.encode(), qs.encode(),
                                      hashlib.sha256).hexdigest()
            r = requests.get(base + path, params=p,
                             headers={"X-MBX-APIKEY": key}, timeout=timeout)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise SafeExchangeError(
                        "INVALID_EXCHANGE_RESPONSE",
                        ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
            err = _classify_http(r.status_code)
            # Kimlik/oran hataları için tekrar deneme anlamsız veya riskli.
            if err.code in ("EXCHANGE_AUTH_FAILED", "EXCHANGE_RATE_LIMITED"):
                raise err
            last_err = err
        except SafeExchangeError:
            raise
        except requests.Timeout:
            last_err = SafeExchangeError("EXCHANGE_TIMEOUT",
                                         ERROR_MESSAGES["EXCHANGE_TIMEOUT"])
        except requests.RequestException:
            last_err = SafeExchangeError(
                "EXCHANGE_UNAVAILABLE", ERROR_MESSAGES["EXCHANGE_UNAVAILABLE"])
        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_BASE * (2 ** attempt))
    raise last_err or SafeExchangeError(
        "EXCHANGE_UNAVAILABLE", ERROR_MESSAGES["EXCHANGE_UNAVAILABLE"])


def _public_get(base: str, path: str, allowlist: set,
                params: dict | None = None, timeout: int = 10) -> Any:
    """Allowlist'li, İMZASIZ, halka açık salt-okunur GET (fiyat vb.).
    Anahtar/imza/özel başlık taşımaz."""
    if ("GET", path) not in allowlist:
        raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
    try:
        r = requests.get(base + path, params=dict(params or {}),
                         timeout=timeout)
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                raise SafeExchangeError(
                    "INVALID_EXCHANGE_RESPONSE",
                    ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
        raise _classify_http(r.status_code)
    except SafeExchangeError:
        raise
    except requests.Timeout:
        raise SafeExchangeError("EXCHANGE_TIMEOUT",
                                ERROR_MESSAGES["EXCHANGE_TIMEOUT"])
    except requests.RequestException:
        raise SafeExchangeError("EXCHANGE_UNAVAILABLE",
                                ERROR_MESSAGES["EXCHANGE_UNAVAILABLE"])


# ── Önbellek çekirdeği ──────────────────────────────────────────────────────

def _freshness_label(kind: str, age: float | None, ok: bool) -> str:
    if not ok or age is None:
        return "UNAVAILABLE"
    return "FRESH" if age <= FRESH_LIMIT[kind] else "STALE"


def _serve(kind: str, source: str, builder: Callable[[], dict]) -> dict:
    """Önbellekli servis: model + meta döndürür. Kaynak hatasında varsa son
    bilinen veri (yaşıyla birlikte) korunur — pano asla tamamen kararmaz."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(kind)
    if hit and now - hit["mono"] < CACHE_TTL[kind]:
        entry = hit
    else:
        try:
            data = builder()
            entry = {"mono": time.monotonic(), "retrieved_at": _now_iso(),
                     "latency_ms": data.pop("_latency_ms", None),
                     "ok": True, "data": data, "error": None}
            with _cache_lock:
                _cache[kind] = entry
        except Exception as raw_exc:
            # Beklenmedik ayrıştırma/çalışma zamanı hatası da kaynak
            # izolasyonunu KIRMAMALI: sterilize hataya eşle (500 yok).
            exc = (raw_exc if isinstance(raw_exc, SafeExchangeError)
                   else SafeExchangeError(
                       "INVALID_EXCHANGE_RESPONSE",
                       ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"]))
            if hit:  # son bilinen veriyi yaşıyla sun
                entry = dict(hit)
                entry["error"] = {"code": exc.code,
                                  "message": exc.message_tr}
            else:
                entry = {"mono": None, "retrieved_at": None,
                         "latency_ms": None, "ok": False, "data": None,
                         "error": {"code": exc.code,
                                   "message": exc.message_tr}}
    age = (time.monotonic() - entry["mono"]) if entry["mono"] else None
    meta = {
        "source": source,
        "retrieved_at": entry["retrieved_at"],
        "age_seconds": round(age, 1) if age is not None else None,
        "freshness": _freshness_label(kind, age, entry["ok"]),
        "latency_ms": entry["latency_ms"],
    }
    out: dict[str, Any] = {"ok": entry["ok"], "meta": meta}
    if entry["data"] is not None:
        out.update(entry["data"])
    if entry.get("error"):
        out["error"] = entry["error"]
    return out


def invalidate_caches(kinds: list[str] | None = None) -> list[str]:
    """Yalnızca güvenli okuma önbelleklerini temizler."""
    with _cache_lock:
        targets = kinds or list(_cache.keys())
        cleared = [k for k in targets if _cache.pop(k, None) is not None]
    return cleared


def mark_full_refresh() -> str:
    global _last_full_refresh
    _last_full_refresh = _now_iso()
    return _last_full_refresh


# ── Binance Global (Spot hesabı — HOTFIX 2100-HF-001) ──────────────────────
# Global panosu ASLA Futures hesap uçlarını kullanmaz; Spot hesabını gösterir.

_spot_log = logging.getLogger("dashboard.spot")


def global_spot_account() -> dict:
    """BINANCE GLOBAL panosu için Spot hesabı (GET /api/v3/account).

    Salt-okunur; emir/çekim/transfer yolu YOK. Sır/imza/özel başlık asla
    loglanmaz."""
    def build() -> dict:
        key, sec = _global_creds()
        if not key or not sec:
            raise SafeExchangeError("EXCHANGE_AUTH_FAILED",
                                    "Salt-okunur anahtar yapılandırılmamış "
                                    "(fail closed).")
        _spot_log.info("Spot Account Request: GET /api/v3/account")
        t0 = time.monotonic()
        try:
            acc = _signed_get(SPOT_BASE, "/api/v3/account", SPOT_ALLOWLIST,
                              key, sec)
        except SafeExchangeError as exc:
            _spot_log.warning("Spot Account Failure: code=%s", exc.code)
            raise
        latency = int((time.monotonic() - t0) * 1000)
        balances = acc.get("balances", [])
        if not isinstance(balances, list):
            _spot_log.warning("Spot Account Failure: code=%s",
                              "INVALID_EXCHANGE_RESPONSE")
            raise SafeExchangeError(
                "INVALID_EXCHANGE_RESPONSE",
                ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
        nonzero = [b for b in balances
                   if _dec_val(b.get("free")) + _dec_val(b.get("locked")) != 0]
        usdt = next((b for b in balances if b.get("asset") == "USDT"), None)
        # Fiyatlama: halka açık, İMZASIZ fiyat listesi; başarısızsa toplam
        # değer KISMİ olarak işaretlenir (asla uydurulmaz).
        prices: dict[str, Decimal] = {}
        valuation = "FULL"
        if any(b.get("asset") != "USDT" for b in nonzero):
            try:
                ticker = _public_get(SPOT_BASE, "/api/v3/ticker/price",
                                     SPOT_ALLOWLIST)
                if isinstance(ticker, list):
                    # Bozuk/sıfır fiyat sözlüğe ALINMAZ → varlık
                    # fiyatlanamaz sayılır ve toplam KISMİ işaretlenir
                    # (sessizce 0 değerleme yok).
                    prices = {
                        t.get("symbol"): _dec_val(t.get("price"))
                        for t in ticker
                        if isinstance(t, dict)
                        and _dec_val(t.get("price")) > 0}
                else:
                    valuation = "PARTIAL"
            except SafeExchangeError:
                valuation = "PARTIAL"
        holdings = []
        total = Decimal(0)
        for b in nonzero:
            asset = b.get("asset")
            qty = _dec_val(b.get("free")) + _dec_val(b.get("locked"))
            if asset == "USDT":
                value = qty
            else:
                px = prices.get(f"{asset}USDT")
                if px is None:
                    valuation = "PARTIAL"
                    value = None
                else:
                    value = qty * px
            if value is not None:
                total += value
            holdings.append({"asset": asset, "amount": str(qty),
                             "value_usdt": (str(value)
                                            if value is not None else None)})
        holdings.sort(key=lambda h: Decimal(h["value_usdt"] or 0),
                      reverse=True)
        _spot_log.info("Spot Account Success: assets=%d total_usdt=%s "
                       "latency_ms=%d", len(nonzero), str(total), latency)
        return {
            "_latency_ms": latency,
            "read_only_auth": "OK",
            "can_trade_flag": bool(acc.get("canTrade")),
            "has_spot_assets": len(nonzero) > 0,
            "total_spot_value_usdt": _dec(total),
            "valuation": valuation,
            "usdt_free": _dec(usdt.get("free")) if usdt else None,
            "usdt_locked": _dec(usdt.get("locked")) if usdt else None,
            "asset_count": len(nonzero),
            "total_asset_count": len(balances),
            "top_holdings": holdings[:5],
            "api_key_masked": mask(key),
        }
    return _serve("global_spot", "BINANCE_GLOBAL_SPOT", build)


# ── Binance Global (USDT-M Futures) ─────────────────────────────────────────

def _global_creds(trading: bool = False) -> tuple[str, str]:
    if trading:
        return (os.environ.get("BINANCE_TRADING_API_KEY", ""),
                os.environ.get("BINANCE_TRADING_API_SECRET", ""))
    return (os.environ.get("BINANCE_API_KEY", ""),
            os.environ.get("BINANCE_API_SECRET", ""))


def global_account() -> dict:
    def build() -> dict:
        key, sec = _global_creds()
        if not key or not sec:
            raise SafeExchangeError("EXCHANGE_AUTH_FAILED",
                                    "Salt-okunur anahtar yapılandırılmamış "
                                    "(fail closed).")
        t0 = time.monotonic()
        acc = _signed_get(GLOBAL_BASE, "/fapi/v2/account", GLOBAL_ALLOWLIST,
                          key, sec)
        try:
            mode_raw = _signed_get(GLOBAL_BASE, "/fapi/v1/positionSide/dualSide",
                                   GLOBAL_ALLOWLIST, key, sec)
            position_mode = ("HEDGE" if mode_raw.get("dualSidePosition")
                             else "ONE_WAY")
        except SafeExchangeError:
            position_mode = "UNKNOWN"
        # İşlem anahtarı doğrulaması (yalnızca GET ile — emir yolu YOK)
        tkey, tsec = _global_creds(trading=True)
        trading_auth = "NOT_CONFIGURED"
        if tkey and tsec:
            try:
                _signed_get(GLOBAL_BASE, "/fapi/v2/balance", GLOBAL_ALLOWLIST,
                            tkey, tsec)
                trading_auth = "OK"
            except SafeExchangeError as exc:
                trading_auth = exc.code
        latency = int((time.monotonic() - t0) * 1000)
        usdt = next((a for a in acc.get("assets", [])
                     if a.get("asset") == "USDT"), {})
        nonzero_assets = [a for a in acc.get("assets", [])
                          if _dec_val(a.get("walletBalance")) != 0]
        return {
            "_latency_ms": latency,
            "read_only_auth": "OK",
            "trading_key_auth": trading_auth,
            "exchange_can_trade": bool(acc.get("canTrade")),
            "app_live_execution": False,   # uygulama canlı emir: her zaman kapalı
            "position_mode": position_mode,
            "usdt_wallet_balance": _dec(usdt.get("walletBalance")),
            "usdt_available_balance": _dec(usdt.get("availableBalance")),
            "usdt_margin_balance": _dec(usdt.get("marginBalance")),
            "unrealized_pnl": _dec(acc.get("totalUnrealizedProfit")),
            "asset_count": len(nonzero_assets),
            "open_position_count": sum(
                1 for p in acc.get("positions", [])
                if _dec_val(p.get("positionAmt")) != 0),
            "open_order_count": sum(
                int(p.get("openOrderInitialMargin", "0") != "0") or 0
                for p in []),  # ayrı openOrders çağrısından gelir (aşağıda)
            "api_key_masked": mask(key),
            "trading_key_masked": mask(tkey) if tkey else None,
        }
    model = _serve("global_account", "BINANCE_GLOBAL_FUTURES", build)
    # Açık emir sayısını (ayrı, kendi önbellekli) emir modelinden al
    if model.get("ok"):
        orders = global_orders()
        model["open_order_count"] = (orders.get("open_order_count")
                                     if orders.get("ok") else None)
    return model


def global_positions(include_zero: bool = False) -> dict:
    def build() -> dict:
        key, sec = _global_creds()
        if not key or not sec:
            raise SafeExchangeError("EXCHANGE_AUTH_FAILED",
                                    "Salt-okunur anahtar yapılandırılmamış "
                                    "(fail closed).")
        t0 = time.monotonic()
        rows = _signed_get(GLOBAL_BASE, "/fapi/v2/positionRisk",
                           GLOBAL_ALLOWLIST, key, sec)
        latency = int((time.monotonic() - t0) * 1000)
        if not isinstance(rows, list):
            raise SafeExchangeError(
                "INVALID_EXCHANGE_RESPONSE",
                ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
        positions = []
        for p in rows:
            amt = _dec_val(p.get("positionAmt"))
            direction = "LONG" if amt > 0 else ("SHORT" if amt < 0 else "FLAT")
            positions.append({
                "symbol": p.get("symbol"),
                "position_amt": _dec(p.get("positionAmt")),
                "direction": direction,
                "entry_price": _dec(p.get("entryPrice")),
                "mark_price": _dec(p.get("markPrice")),
                "unrealized_pnl": _dec(p.get("unRealizedProfit")),
                "leverage": _dec(p.get("leverage")),
                "liquidation_price": _dec(p.get("liquidationPrice")),
                "margin_type": p.get("marginType"),
                "isolated_wallet": _dec(p.get("isolatedWallet")),
                "update_time": p.get("updateTime"),
            })
        return {"_latency_ms": latency, "positions_all": positions}
    model = _serve("global_positions", "BINANCE_GLOBAL_FUTURES", build)
    if model.get("ok"):
        all_rows = model.pop("positions_all", [])
        active = [p for p in all_rows if p["direction"] != "FLAT"]
        model["positions"] = all_rows if include_zero else active
        model["open_position_count"] = len(active)
        model["include_zero"] = include_zero
    return model


def global_orders() -> dict:
    def build() -> dict:
        key, sec = _global_creds()
        if not key or not sec:
            raise SafeExchangeError("EXCHANGE_AUTH_FAILED",
                                    "Salt-okunur anahtar yapılandırılmamış "
                                    "(fail closed).")
        t0 = time.monotonic()
        rows = _signed_get(GLOBAL_BASE, "/fapi/v1/openOrders",
                           GLOBAL_ALLOWLIST, key, sec)
        latency = int((time.monotonic() - t0) * 1000)
        if not isinstance(rows, list):
            raise SafeExchangeError(
                "INVALID_EXCHANGE_RESPONSE",
                ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
        orders = [{
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "type": o.get("type"),
            "status": o.get("status"),
            "orig_qty": _dec(o.get("origQty")),
            "executed_qty": _dec(o.get("executedQty")),
            "price": _dec(o.get("price")),
            "stop_price": _dec(o.get("stopPrice")),
            "reduce_only": bool(o.get("reduceOnly")),
            "time": o.get("time"),
            "update_time": o.get("updateTime"),
        } for o in rows]
        return {"_latency_ms": latency, "orders": orders,
                "open_order_count": len(orders)}
    return _serve("global_orders", "BINANCE_GLOBAL_FUTURES", build)


# ── Binance TR ──────────────────────────────────────────────────────────────

def tr_account() -> dict:
    def build() -> dict:
        key = os.environ.get("BINANCE_TR_API_KEY", "")
        sec = os.environ.get("BINANCE_TR_API_SECRET", "")
        if not key or not sec:
            raise SafeExchangeError("EXCHANGE_AUTH_FAILED",
                                    "Binance TR anahtarı yapılandırılmamış "
                                    "(fail closed).")
        t0 = time.monotonic()
        body = _signed_get(TR_BASE, "/open/v1/account/spot", TR_ALLOWLIST,
                           key, sec)
        latency = int((time.monotonic() - t0) * 1000)
        if not isinstance(body, dict) or body.get("code", 0) not in (0, "0"):
            raise SafeExchangeError(
                "INVALID_EXCHANGE_RESPONSE",
                ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
        data = body.get("data")
        # TR API: data list VEYA dict olabilir (bkz. Mission 1310A)
        if isinstance(data, list):
            assets = [a for a in data if isinstance(a, dict)]
            status = None
        else:
            assets = [a for a in (data or {}).get("accountAssets") or []
                      if isinstance(a, dict)]
            status = (data or {}).get("status")

        def bal(sym: str, field: str) -> str:
            row = next((a for a in assets if a.get("asset") == sym), None)
            return _dec(row.get(field)) if row else "0"

        nonzero = [a for a in assets
                   if _dec_val(a.get("free")) or _dec_val(a.get("locked"))]
        return {
            "_latency_ms": latency,
            "auth_status": "OK",
            "account_status": status if status is not None else "ACTIVE",
            "try_free": bal("TRY", "free"),
            "try_locked": bal("TRY", "locked"),
            "usdt_free": bal("USDT", "free"),
            "usdt_locked": bal("USDT", "locked"),
            "asset_count": len(assets),
            "nonzero_asset_count": len(nonzero),
            "api_key_masked": mask(key),
        }
    return _serve("tr_account", "BINANCE_TR", build)


def tr_movements_summary() -> dict:
    """Mission 1310B'nin deterministik ledger çıktısını okur — panoyu açmak
    ledger'ı ASLA yeniden beslemez veya değiştirmez (salt okuma)."""
    def build() -> dict:
        t0 = time.monotonic()
        if not LEDGER_PATH.exists():
            raise SafeExchangeError("EXCHANGE_UNAVAILABLE",
                                    "Hareket defteri henüz oluşturulmamış "
                                    "(Mission 1310B çalıştırılmalı).")
        events = json.loads(LEDGER_PATH.read_text())
        report = (json.loads(M1310B_REPORT.read_text())
                  if M1310B_REPORT.exists() else {})
        latency = int((time.monotonic() - t0) * 1000)
        classes = [e.get("class") for e in events]
        times = sorted(e.get("timestamp_iso") for e in events
                       if e.get("timestamp_iso"))
        unexplained = {
            k: str(v) for k, v in (report.get("unexplained_differences")
                                   or {}).items()}
        return {
            "_latency_ms": latency,
            "deposit_count": classes.count("DEPOSIT"),
            "withdrawal_count": classes.count("WITHDRAWAL"),
            "internal_transfer_count": classes.count("INTERNAL_TRANSFER"),
            "earliest_movement": times[0] if times else None,
            "latest_movement": times[-1] if times else None,
            "ledger_event_count": len(events),
            "duplicate_block_count": report.get("duplicates_blocked", 0),
            "reconciliation": report.get("reconciliation", "PARTIAL"),
            "unexplained_differences": unexplained,
            "coverage_warning": ("Hareket API'si alım-satım işlemlerini "
                                 "içermez; mutabakat tasarım gereği "
                                 "KISMİ'dir (bkz. Mission 1310B)."),
        }
    return _serve("tr_movements", "BINANCE_TR", build)


# ── Sistem ve genel bakış ───────────────────────────────────────────────────

def system_status(app_info: dict) -> dict:
    with _cache_lock:
        cached = {k: {"age_seconds": round(time.monotonic() - v["mono"], 1)
                      if v.get("mono") else None,
                      "ok": v.get("ok", False)}
                  for k, v in _cache.items()}
    ledger_ok = LEDGER_PATH.exists()
    return {
        "application": "ok",
        "uptime_seconds": int(time.monotonic() - _started_monotonic),
        "version": app_info.get("version"),
        "mode": app_info.get("mode"),
        "setup_state": app_info.get("setup_state"),
        "sources": cached,
        "ledger_integrity": "OK" if ledger_ok else "NOT_INITIALIZED",
        "last_full_refresh": _last_full_refresh,
        "write_counters": dict(WRITE_COUNTERS),
        "secret_exposure_last_scan": 0,
        "server_time": _now_iso(),
    }


def overview(app_info: dict) -> dict:
    """Tüm kaynakların sterilize toplaması. Tek kaynak hatası diğerlerini
    ETKİLEMEZ; birleşik yanıltıcı portföy toplamı üretilmez."""
    gs = global_spot_account()
    ga = global_account()
    gp = global_positions()
    go = global_orders()
    ta = tr_account()
    tm = tr_movements_summary()
    warnings: list[str] = []
    for name, m in (("Binance Global (Spot)", gs),
                    ("Binance Futures", ga), ("Pozisyonlar", gp),
                    ("Emirler", go), ("Binance TR", ta),
                    ("TR Hareketleri", tm)):
        if not m.get("ok"):
            warnings.append(f"{name}: "
                            f"{(m.get('error') or {}).get('message', 'kullanılamıyor')}")
        elif m["meta"]["freshness"] == "STALE":
            warnings.append(f"{name}: veri eski "
                            f"({m['meta']['age_seconds']} sn)")
    if tm.get("ok") and tm.get("reconciliation") == "PARTIAL":
        warnings.append("Binance TR mutabakatı KISMİ (tasarım gereği — "
                        "hareket API'si trade içermez).")
    return {
        "application": {
            "mode": app_info.get("mode"),
            "setup_state": app_info.get("setup_state"),
            "live_trading_enabled": False,
            "dry_run_enabled": app_info.get("dry_run_enabled", False),
            "transfers_enabled": False,
            "withdrawals_enabled": False,
            "server_time": _now_iso(),
        },
        "global_spot": gs,
        "global_futures": ga,
        "positions": gp,
        "orders": go,
        "tr": ta,
        "tr_movements": tm,
        "system": system_status(app_info),
        "warnings": warnings,
        "last_full_refresh": _last_full_refresh,
    }
