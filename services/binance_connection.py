"""Binance Connection Agent servisi — SALT OKUNUR bağlantı yönetimi.

Tek kaynak: dashboard/API/agent registry hep bu servisi kullanır.
Yetenekler: bağlantı testi, izin kontrolü, durum sınıflandırma, güvenli
saklama (exchange_credentials: Windows'ta DPAPI şifreli), audit log.

CANLI EMİR YOLU YOK: yalnız read-only allowlist'li sorgular. Secret'lar
log/response/exception içine asla yazılmaz.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SNAPSHOT_PATH = DATA_DIR / "binance_connection_status.json"
AUDIT_PATH = DATA_DIR / "connection_audit.jsonl"

PROVIDERS = ("BINANCE_GLOBAL", "BINANCE_TR")

# Standart durum modeli (dashboard/API/registry aynı sözlüğü kullanır)
STATUSES = (
    "NOT_CONFIGURED", "TESTING", "CONNECTED_READ_ONLY",
    "CONNECTED_PERMISSIONS_UNVERIFIED", "INVALID_CREDENTIALS",
    "IP_RESTRICTED", "PERMISSION_DENIED", "TIMESTAMP_DRIFT",
    "NETWORK_DEGRADED", "DISCONNECTED", "ERROR",
)

FUTURES_STATUS = "NOT_TESTED"  # Spot-only mimari: imzalı /fapi tombstone
FUTURES_NOTE = ("Spot-only mimari: imzalı Futures erişimi bu üründen "
                "kalıcı olarak kaldırıldı (FUTURES_REMOVED sözleşmesi); "
                "Futures erişimi test edilmez.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mask(key: str) -> str:
    import exchange_credentials as xc
    return xc.mask_key(key or "")


# Rotasyon eşiği: dosya bu boyutu aşınca son AUDIT_KEEP_LINES satır tutulur.
AUDIT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
AUDIT_KEEP_LINES = 20_000


def _rotate_audit_if_needed() -> None:
    """Audit dosyası eşiği aşarsa son N satırı koruyarak kırp.

    Bütünlük: kırpma atomiktir (tmp dosyaya yaz + replace); hata olursa
    mevcut dosya olduğu gibi kalır ve akış durmaz.
    """
    try:
        if AUDIT_PATH.stat().st_size <= AUDIT_MAX_BYTES:
            return
        with open(AUDIT_PATH, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        kept = lines[-AUDIT_KEEP_LINES:]
        dropped = len(lines) - len(kept)
        marker = json.dumps({
            "ts": _now(), "event": "audit_rotated", "provider": "",
            "masked_api_key": "", "result_code": f"DROPPED_{dropped}_LINES",
            "environment": "replit" if os.environ.get("REPL_ID") else "local",
            "source": "windows_local" if os.name == "nt" else "replit",
        }, ensure_ascii=False)
        tmp = AUDIT_PATH.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(marker + "\n")
            fh.writelines(kept)
        os.replace(tmp, AUDIT_PATH)
    except OSError:
        pass  # rotasyon hatası akışı durdurmaz


def audit(event: str, provider: str, masked_api_key: str = "",
          result_code: str = "") -> None:
    """Secret içermeyen audit kaydı (JSONL, append-only + boyut rotasyonu)."""
    rec = {"ts": _now(), "event": event, "provider": provider,
           "masked_api_key": masked_api_key, "result_code": result_code,
           "environment": "replit" if os.environ.get("REPL_ID") else "local",
           "source": "windows_local" if os.name == "nt" else "replit"}
    try:
        DATA_DIR.mkdir(exist_ok=True)
        _rotate_audit_if_needed()
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # audit yazılamazsa akış durmaz (secret riski yok)


def _load_snapshot() -> dict:
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_snapshot(provider: str, entry: dict) -> None:
    snap = _load_snapshot()
    snap[provider] = entry
    try:
        DATA_DIR.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(snap, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
    except OSError:
        pass


def classify_error(exc: Exception) -> str:
    """Her tür istisnayı standart durum koduna indirger (secret'sız)."""
    name = type(exc).__name__
    kind = str(getattr(exc, "kind", "") or "")
    xcode = getattr(exc, "exchange_code", None)
    msg = (str(getattr(exc, "exchange_message", "")) or str(exc))[:200]
    low = msg.lower()
    try:
        code_i = int(xcode) if xcode is not None else None
    except (TypeError, ValueError):
        code_i = None
    if code_i in (-2014, -1022, -2015):
        # -2015: "Invalid API-key, IP, or permissions" → IP ipucu varsa ayır
        if code_i == -2015 and ("ip" in low):
            return "IP_RESTRICTED"
        return "INVALID_CREDENTIALS"
    if code_i == -1021:
        return "TIMESTAMP_DRIFT"
    if code_i in (-2011, -1002) or "permission" in low or "unauthorized" in low:
        return "PERMISSION_DENIED"
    if "ssl" in name.lower() or "TLS" in kind or "SSL" in kind:
        return "NETWORK_DEGRADED"
    if "DNS" in kind or "getaddrinfo" in low or "name resolution" in low:
        return "NETWORK_DEGRADED"
    if kind in ("TIMEOUT", "CONNECTION") or "timeout" in name.lower():
        return "NETWORK_DEGRADED"
    if kind == "EXCHANGE_ERROR" and getattr(exc, "http_status", 0) in (418, 429):
        return "NETWORK_DEGRADED"
    status = getattr(exc, "http_status", None)
    if status in (401, 403):
        return "INVALID_CREDENTIALS"
    if status in (418, 429):
        return "NETWORK_DEGRADED"
    return "ERROR"


def _global_permission_review(acct: dict) -> tuple[str, dict]:
    """Global hesap yanıtından izin politikası kararı.

    canWithdraw=true → REDDET. canTrade=true → REDDET (salt-okunur hedef;
    kullanıcı Binance panelinden yetkiyi kapatmalı). Alanlar hiç yoksa
    UNVERIFIED."""
    fields = {k: acct.get(k) for k in
              ("canTrade", "canWithdraw", "canDeposit", "brokered",
               "permissions", "accountType") if k in acct}
    if "canWithdraw" not in acct and "canTrade" not in acct:
        return "CONNECTED_PERMISSIONS_UNVERIFIED", fields
    if bool(acct.get("canWithdraw")):
        return "PERMISSION_DENIED", fields
    if bool(acct.get("canTrade")):
        return "PERMISSION_DENIED", fields
    return "CONNECTED_READ_ONLY", fields


def test_global(api_key: str, api_secret: str) -> dict:
    """Binance Global read-only bağlantı testi (kaydetmeden).

    Sıra: public time → offset ölçümü → imzalı hesap (istemci sunucu
    zamanını kullanır; drift'te otomatik offset) → izin kontrolü."""
    import binance_global_client as bgc
    result: dict[str, Any] = {"provider": "BINANCE_GLOBAL",
                              "tested_at": _now(),
                              "futures": FUTURES_STATUS,
                              "futures_note": FUTURES_NOTE}
    client = bgc.BinanceGlobalClient(api_key, api_secret)
    try:
        server_ms = client.get_server_time()
        result["time_offset_ms"] = server_ms - int(time.time() * 1000)
    except Exception as exc:
        result["status"] = classify_error(exc)
        result["error_code"] = result["status"]
        return result
    last_exc: Exception | None = None
    for attempt in range(2):  # drift durumunda taze sunucu zamanıyla tekrar
        try:
            acct = client.get_spot_account()
            status, fields = _global_permission_review(
                acct if isinstance(acct, dict) else {})
            result["status"] = status
            result["account_type"] = (acct or {}).get("accountType")
            result["permission_fields"] = sorted(fields.keys())
            if status == "PERMISSION_DENIED":
                result["error_code"] = "PERMISSION_DENIED"
                result["guidance"] = (
                    "Anahtar işlem/çekim yetkisi taşıyor. Binance API "
                    "yönetiminden yalnız 'Enable Reading' yetkili anahtar "
                    "kullanın; bu anahtar KAYDEDİLMEDİ.")
            return result
        except Exception as exc:
            last_exc = exc
            if classify_error(exc) != "TIMESTAMP_DRIFT" or attempt:
                break
    result["status"] = classify_error(last_exc) if last_exc else "ERROR"
    result["error_code"] = result["status"]
    if result["status"] == "IP_RESTRICTED":
        result["guidance"] = ("Binance API yönetiminde bu makinenin dış "
                              "IP'sine izin verin (anahtar korunuyor; "
                              "yeniden girmeniz gerekmez).")
    return result


def test_tr(api_key: str, api_secret: str) -> dict:
    """Binance TR read-only bağlantı testi (resmî TR adaptörü ile)."""
    import binance_tr_client as btr
    result: dict[str, Any] = {"provider": "BINANCE_TR",
                              "tested_at": _now(),
                              "futures": "NOT_TESTED"}
    client = btr.BinanceTRClient(api_key, api_secret)
    try:
        server_ms = client.get_server_time()
        result["time_offset_ms"] = server_ms - int(time.time() * 1000)
        body = client.get_spot_account()
    except Exception as exc:
        result["status"] = classify_error(exc)
        result["error_code"] = result["status"]
        return result
    data = body.get("data") if isinstance(body, dict) else None
    data = data if isinstance(data, dict) else (body or {})
    if bool(data.get("canWithdraw")) or bool(data.get("canTrade")):
        result["status"] = "PERMISSION_DENIED"
        result["error_code"] = "PERMISSION_DENIED"
        result["guidance"] = ("Anahtar işlem/çekim yetkisi taşıyor; yalnız "
                              "okuma yetkili anahtar kullanın.")
        return result
    if "canWithdraw" in data or "canTrade" in data:
        result["status"] = "CONNECTED_READ_ONLY"
    else:
        # TR API izin alanı vermiyorsa dürüst sarı durum.
        result["status"] = "CONNECTED_PERMISSIONS_UNVERIFIED"
    return result


_TESTERS = {"BINANCE_GLOBAL": test_global, "BINANCE_TR": test_tr}


def connect(provider: str, api_key: str, api_secret: str) -> dict:
    """Test → izin doğrulama → yalnız güvenliyse şifreli sakla."""
    import exchange_credentials as xc
    if provider not in PROVIDERS:
        raise ValueError("bilinmeyen sağlayıcı")
    masked = _mask(api_key)
    audit("connection_attempt", provider, masked)
    api_key = (api_key or "").strip()
    api_secret = (api_secret or "").strip()
    if not api_key or not api_secret:
        return {"provider": provider, "status": "INVALID_CREDENTIALS",
                "error_code": "EMPTY", "tested_at": _now()}
    result = _TESTERS[provider](api_key, api_secret)
    ok_states = ("CONNECTED_READ_ONLY", "CONNECTED_PERMISSIONS_UNVERIFIED")
    if result.get("status") in ok_states:
        try:
            xc.save_local(provider, api_key, api_secret)
            audit("connection_success", provider, masked,
                  result["status"])
            audit("credential_updated", provider, masked)
        except Exception:
            result["status"] = "ERROR"
            result["error_code"] = "STORE_FAILED"
            audit("connection_failure", provider, masked, "STORE_FAILED")
    elif result.get("status") == "PERMISSION_DENIED":
        audit("permission_rejected", provider, masked)
    else:
        audit("connection_failure", provider, masked,
              str(result.get("status")))
    entry = {k: v for k, v in result.items() if k != "provider"}
    entry["masked_api_key"] = masked if result.get("status") in ok_states \
        else ""
    _save_snapshot(provider, entry)
    return result


def test_stored(provider: str) -> dict:
    """Kayıtlı credential ile yeniden test (Test Et butonu)."""
    import exchange_credentials as xc
    if provider not in PROVIDERS:
        raise ValueError("bilinmeyen sağlayıcı")
    key, sec = xc.credentials(provider)
    if not key or not sec:
        result = {"provider": provider, "status": "NOT_CONFIGURED",
                  "tested_at": _now()}
    else:
        result = _TESTERS[provider](key, sec)
        result["masked_api_key"] = _mask(key)
    entry = {k: v for k, v in result.items() if k != "provider"}
    _save_snapshot(provider, entry)
    audit("connection_attempt", provider, _mask(key),
          str(result.get("status")))
    return result


def disconnect(provider: str) -> dict:
    import exchange_credentials as xc
    if provider not in PROVIDERS:
        raise ValueError("bilinmeyen sağlayıcı")
    removed = xc.remove_local(provider)
    audit("credential_removed", provider, xc.masked_key(provider))
    _save_snapshot(provider, {"status": "DISCONNECTED",
                              "tested_at": _now()})
    return {"provider": provider, "status": "DISCONNECTED",
            "removed": bool(removed)}


def status() -> dict:
    """İki sağlayıcının anlık durumu (secret'sız; dashboard/registry)."""
    import exchange_credentials as xc
    snap = _load_snapshot()
    out: dict[str, Any] = {"live_orders": "DISABLED"}
    for provider in PROVIDERS:
        entry = dict(snap.get(provider) or {})
        configured = xc.configured(provider)
        if not configured:
            entry = {"status": "NOT_CONFIGURED"}
        else:
            entry.setdefault("status", "CONNECTED_PERMISSIONS_UNVERIFIED")
            entry["masked_api_key"] = xc.masked_key(provider)
            entry["source"] = xc.source(provider)
        entry.pop("guidance_secret", None)
        out[provider] = entry
    return out
