"""Salt-okunur borsa geçidi (Mission 1400).

Tüm borsa istekleri YALNIZCA backend'den çıkar; tarayıcı asla borsa ile
konuşmaz. Yalnızca GET; allowlist ağ isteğinden ÖNCE uygulanır. Emir,
transfer, çekim veya kaldıraç/pozisyon değişikliği yapan hiçbir kod yolu
yoktur. Secret'lar hiçbir yanıt gövdesine, log'a veya dosyaya yazılmaz;
anahtarlar yalnızca maskeli (ilk4…son4) raporlanır. Fail closed: secret
eksikse ağ isteği yapılmadan 'configured: false' döner.

Yanıtlar kısa süreli önbelleğe alınır (rate limit koruması).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
import urllib.parse
from decimal import Decimal
from typing import Any

import requests

GLOBAL_BASE = "https://fapi.binance.com"
TR_BASE = "https://www.binance.tr"  # tek adaptör: binance_tr_client (eski trbinance.com KULLANILMAZ)

# Yalnızca GET; ağ isteğinden önce zorunlu.
GLOBAL_ALLOWLIST = {
    ("GET", "/fapi/v2/balance"),
    ("GET", "/fapi/v2/account"),
    ("GET", "/fapi/v2/positionRisk"),
}
TR_ALLOWLIST = {
    ("GET", "/open/v1/account/spot"),
}

CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


def _signed_get(base: str, path: str, allowlist: set, key: str,
                secret: str, params: dict | None = None,
                timeout: int = 15) -> requests.Response:
    if ("GET", path) not in allowlist:
        raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    qs = urllib.parse.urlencode(p)
    p["signature"] = hmac.new(secret.encode(), qs.encode(),
                              hashlib.sha256).hexdigest()
    return requests.get(base + path, params=p,
                        headers={"X-MBX-APIKEY": key}, timeout=timeout)


def _cached(name: str, builder) -> dict:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(name)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    data = builder()
    with _cache_lock:
        _cache[name] = (time.monotonic(), data)
    return data


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _fail_closed(source: str) -> dict:
    return {"source": source, "configured": False, "ok": False,
            "error": "secret yapılandırılmamış (fail closed — istek "
                     "gönderilmedi)"}


def global_futures_summary() -> dict[str, Any]:
    """Binance Global USDT-M futures bakiye özeti (salt okunur anahtar)."""
    key = os.environ.get("BINANCE_API_KEY", "")
    sec = os.environ.get("BINANCE_API_SECRET", "")
    if not key or not sec:
        return _fail_closed("binance_global_futures")

    def build() -> dict:
        try:
            r = _signed_get(GLOBAL_BASE, "/fapi/v2/balance",
                            GLOBAL_ALLOWLIST, key, sec)
            if r.status_code != 200:
                return {"source": "binance_global_futures",
                        "configured": True, "ok": False,
                        "key_masked": mask(key),
                        "error": f"HTTP {r.status_code}"}
            rows = r.json()
            balances = [{"asset": b["asset"],
                         "balance": b.get("balance"),
                         "available": b.get("availableBalance")}
                        for b in rows
                        if Decimal(str(b.get("balance") or 0)) != 0]
            return {"source": "binance_global_futures", "configured": True,
                    "ok": True, "key_masked": mask(key),
                    "balances": balances, "read_only": True}
        except Exception as exc:  # bozuk/beklenmedik yanıt → güvenli hata
            return {"source": "binance_global_futures", "configured": True,
                    "ok": False, "key_masked": mask(key),
                    "error": f"yanıt hatası: {type(exc).__name__}"}

    return _cached("global_futures", build)


def tr_spot_summary() -> dict[str, Any]:
    """Binance TR spot bakiye özeti (salt okunur anahtar)."""
    key = os.environ.get("BINANCE_TR_API_KEY", "")
    sec = os.environ.get("BINANCE_TR_API_SECRET", "")
    if not key or not sec:
        return _fail_closed("binance_tr_spot")

    def build() -> dict:
        try:
            # Tek adaptör: tüm Binance TR erişimi binance_tr_client üzerinden.
            import binance_tr_client as btr
            body = btr.BinanceTRClient(key, sec).get_spot_account()
            data = body.get("data")
            if isinstance(data, list):
                accs = data
            else:
                accs = (data or {}).get("accountAssets") or []
            balances = [{"asset": a.get("asset"), "free": a.get("free"),
                         "locked": a.get("locked")}
                        for a in accs if isinstance(a, dict)
                        and (Decimal(str(a.get("free") or 0))
                             or Decimal(str(a.get("locked") or 0)))]
            return {"source": "binance_tr_spot", "configured": True,
                    "ok": True, "key_masked": mask(key),
                    "balances": balances, "read_only": True}
        except Exception as exc:  # bozuk/beklenmedik yanıt → güvenli hata
            out = {"source": "binance_tr_spot", "configured": True,
                   "ok": False, "key_masked": mask(key),
                   "error": f"yanıt hatası: {type(exc).__name__}"}
            xc = getattr(exc, "exchange_code", None)
            if xc is not None:
                out["exchange_code"] = xc
                out["exchange_message"] = getattr(
                    exc, "exchange_message", "")
            return out

    return _cached("tr_spot", build)


def exchange_summary() -> dict[str, Any]:
    """Dashboard için birleşik salt-okunur borsa özeti."""
    return {
        "live_trading": False,      # Mission 1400: otomatik canlı emir YOK
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "global_futures": global_futures_summary(),
        "tr_spot": tr_spot_summary(),
    }
