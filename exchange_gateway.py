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

import os
import threading
import time
from decimal import Decimal
from typing import Any

TR_BASE = "https://www.binance.tr"  # tek adaptör: binance_tr_client (eski trbinance.com KULLANILMAZ)

# İmzalı hesap fetch'i artık kanonik hesap servisindedir
# (dashboard_api._tr_account_raw); gateway kendi imzalı yolunu TAŞIMAZ.
CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()



def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


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


def tr_spot_summary() -> dict[str, Any]:
    """Binance TR spot bakiye özeti (salt okunur anahtar).

    Kanonik hesap servisine delege eder: dashboard_api._tr_account_raw()
    paylaşımlı ham yanıtı kullanılır — gateway kendi imzalı fetch'ini
    YAPMAZ (tek hesap doğruluk kaynağı)."""
    import exchange_credentials as xc
    key, sec = xc.credentials("BINANCE_TR")
    if not key or not sec:
        return _fail_closed("binance_tr_spot")

    def build() -> dict:
        try:
            import dashboard_api as dapi
            body, _latency = dapi._tr_account_raw()
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
    """Dashboard için birleşik salt-okunur borsa özeti (Spot-only)."""
    return {
        "live_trading": False,      # Mission 1400: otomatik canlı emir YOK
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "tr_spot": tr_spot_summary(),
    }
