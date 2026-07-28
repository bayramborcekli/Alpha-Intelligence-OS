"""Mission 1400.3 — Portföy / Pozisyon / Emir servis katmanı (salt okunur).

dashboard_api üzerine kuruludur: aynı GET-only allowlist, önbellek ve
tazelik politikası. Emir/iptal/transfer/çekim kod yolu YOKTUR; yazma
sayaçları dashboard_api.WRITE_COUNTERS içinde sıfır kalır. CSV dışa
aktarımlar sterilize tipli modellerden sunucu tarafında üretilir ve formül
enjeksiyonuna karşı korunur.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import dashboard_api as dapi
from dashboard_api import (SafeExchangeError, _dec, _dec_val, _serve,
                           _signed_get, TR_ALLOWLIST, TR_BASE)

# Yeni önbellek türleri (merkezî politikaya kayıt)
dapi.CACHE_TTL.setdefault("tr_assets", 30)
dapi.FRESH_LIMIT.setdefault("tr_assets", 60)
dapi.CACHE_TTL.setdefault("global_assets", 15)
dapi.FRESH_LIMIT.setdefault("global_assets", 60)

MAX_ROWS = 500          # istek başına üst sınır
DEFAULT_LIMIT = 100
MAX_SEARCH_LEN = 32

REFRESH_SCOPES = {
    "portfolio": ["global_assets", "tr_assets", "tr_account"],
}

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class InvalidParameter(ValueError):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _parse_bool(val: str | None, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    v = val.lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    raise InvalidParameter("boolean")


def _parse_search(val: str | None) -> str:
    if not val:
        return ""
    s = val.strip()
    if len(s) > MAX_SEARCH_LEN:
        raise InvalidParameter("search")
    # yalnızca güvenli karakterler (sembol/varlık adları)
    if not all(c.isalnum() or c in "-_/. " for c in s):
        raise InvalidParameter("search")
    return s.upper()


def _parse_enum(val: str | None, allowed: set[str], name: str,
                default: str | None = None) -> str | None:
    if val is None or val == "":
        return default
    v = val.lower() if name in ("sort", "order") else val.upper()
    if v not in allowed:
        raise InvalidParameter(name)
    return v


def _parse_limit(val: str | None) -> int:
    if val is None or val == "":
        return DEFAULT_LIMIT
    try:
        n = int(val)
    except ValueError:
        raise InvalidParameter("limit")
    if n < 1 or n > MAX_ROWS:
        raise InvalidParameter("limit")
    return n


def _sort_rows(rows: list[dict], key: str, numeric: bool,
               descending: bool) -> list[dict]:
    if numeric:
        return sorted(rows, key=lambda r: _dec_val(r.get(key)),
                      reverse=descending)
    return sorted(rows, key=lambda r: str(r.get(key) or ""),
                  reverse=descending)


def global_assets() -> dict:
    """Binance Global SPOT hesap varlıkları (tam liste, tipli).

    Spot-only mimari: /fapi çağrısı YOK; GET /api/v3/account kullanılır."""
    def build() -> dict:
        import time
        key, sec = dapi._global_creds()
        if not key or not sec:
            raise SafeExchangeError("EXCHANGE_AUTH_FAILED",
                                    "Salt-okunur anahtar yapılandırılmamış "
                                    "(fail closed).")
        t0 = time.monotonic()
        acc = _signed_get(dapi.SPOT_BASE, "/api/v3/account",
                          dapi.SPOT_ALLOWLIST, key, sec)
        latency = int((time.monotonic() - t0) * 1000)
        balances = acc.get("balances", [])
        if not isinstance(balances, list):
            raise SafeExchangeError(
                "INVALID_EXCHANGE_RESPONSE",
                dapi.ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
        assets = []
        for a in balances:
            if not isinstance(a, dict):
                continue
            total = _dec_val(a.get("free")) + _dec_val(a.get("locked"))
            assets.append({
                "asset": str(a.get("asset") or "?"),
                "free": _dec(a.get("free")),
                "locked": _dec(a.get("locked")),
                "total": _dec(total),
                "update_time": None,
                "nonzero": total != 0,
            })
        return {"_latency_ms": latency, "assets": assets[:MAX_ROWS],
                "asset_count": len(assets),
                "nonzero_asset_count": sum(1 for a in assets if a["nonzero"])}
    return _serve("global_assets", "BINANCE_GLOBAL_SPOT", build)


def tr_assets() -> dict:
    """Binance TR spot varlıkları (tam liste, Decimal toplamlı)."""
    def build() -> dict:
        import os, time
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
                dapi.ERROR_MESSAGES["INVALID_EXCHANGE_RESPONSE"])
        data = body.get("data")
        if isinstance(data, list):
            raw, status = [a for a in data if isinstance(a, dict)], None
        else:
            raw = [a for a in (data or {}).get("accountAssets") or []
                   if isinstance(a, dict)]
            status = (data or {}).get("status")
        assets = []
        for a in raw:
            free, locked = _dec_val(a.get("free")), _dec_val(a.get("locked"))
            assets.append({
                "asset": str(a.get("asset") or "?"),
                "free": _dec(a.get("free")),
                "locked": _dec(a.get("locked")),
                "total": str(free + locked),   # Decimal toplam
                "update_time": a.get("updateTime"),
                "nonzero": (free + locked) != 0,
            })
        return {"_latency_ms": latency, "assets": assets[:MAX_ROWS],
                "asset_count": len(assets),
                "nonzero_asset_count": sum(1 for a in assets if a["nonzero"]),
                "account_status": status if status is not None else "ACTIVE"}
    return _serve("tr_assets", "BINANCE_TR", build)


# ── Portföy toplaması (birleşik toplam ÜRETİLMEZ) ───────────────────────────

PORTFOLIO_SORTS = {"asset": False, "wallet_balance": True, "free": True,
                   "total": True}


def _filter_assets(rows: list[dict], include_zero: bool, search: str,
                   sort: str | None, order: str, numeric_default: str,
                   limit: int) -> list[dict]:
    out = [r for r in rows if include_zero or r.get("nonzero")]
    if search:
        out = [r for r in out if search in r["asset"].upper()]
    if sort and out:
        # Bölümde bulunmayan sıralama alanı → bölümün sayısal varsayılanı
        key = sort if sort in out[0] else numeric_default
        out = _sort_rows(out, key, PORTFOLIO_SORTS.get(key, True),
                         order == "desc")
    return out[:limit]


def portfolio(app_mode: str, include_zero: bool = False, search: str = "",
              sort: str | None = None, order: str = "asc",
              limit: int = DEFAULT_LIMIT) -> dict:
    """Ayrık hesap bölümleri. TRY→USDT dönüşümü ve kaynaklar arası toplam
    kasıtlı olarak YOKTUR (yanıltıcı birleşik değer üretilmez)."""
    ga, ta = global_assets(), tr_assets()
    sections, warnings = [], []
    for label, model, num_default in (
            ("BINANCE_GLOBAL_SPOT", ga, "total"),
            ("BINANCE_TR", ta, "total")):
        sec: dict[str, Any] = {"source": label, "meta": model.get("meta"),
                               "ok": model.get("ok", False)}
        if model.get("ok"):
            rows = model.get("assets") or []
            sec["assets"] = _filter_assets(rows, include_zero, search, sort,
                                           order, num_default, limit)
            sec["asset_count"] = model.get("asset_count", 0)
            sec["nonzero_asset_count"] = model.get("nonzero_asset_count", 0)
            if label == "BINANCE_TR":
                sec["account_status"] = model.get("account_status")
            if model["meta"]["freshness"] == "STALE":
                warnings.append(f"{label}: veri eski "
                                f"({model['meta']['age_seconds']} sn)")
        else:
            sec["assets"] = []
            sec["error"] = model.get("error")
            warnings.append(f"{label}: "
                            f"{(model.get('error') or {}).get('message', 'kullanılamıyor')}")
        sections.append(sec)
    return {
        "application_mode": app_mode,
        "live_execution_enabled": False,
        "sections": sections,
        "warnings": warnings,
        "note": ("Hesaplar ayrı para birimlerindedir; yanıltıcı birleşik "
                 "toplam kasıtlı olarak gösterilmez."),
    }


def positions_view(include_zero: bool = False) -> dict:
    """KALDIRILDI — Futures kaldırıldı (Spot-only mimari)."""
    return {"ok": False, "error": {"code": "FUTURES_REMOVED",
            "message": "Futures kaldırıldı (Spot-only mimari)."},
            "meta": None, "positions": []}


def orders_view() -> dict:
    """KALDIRILDI — Futures kaldırıldı (Spot-only mimari)."""
    return {"ok": False, "error": {"code": "FUTURES_REMOVED",
            "message": "Futures kaldırıldı (Spot-only mimari)."},
            "meta": None, "orders": []}


def _csv_text(v: Any) -> str:
    """Metin hücreleri: formül enjeksiyonuna karşı nötralize edilir."""
    s = "" if v is None else str(v)
    if s.startswith(_FORMULA_PREFIXES):
        return "'" + s
    return s


def _csv_num(v: Any) -> str:
    """Sayısal hücreler: ham Decimal string — negatif değerler DEĞİŞMEZ."""
    return "" if v is None else str(v)


def _csv_response_body(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows[:MAX_ROWS]:
        w.writerow(r)
    # UTF-8 BOM: Türkçe Excel uyumluluğu
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def portfolio_csv(include_zero: bool, search: str, sort: str | None,
                  order: str, limit: int) -> tuple[bytes, str]:
    data = portfolio("PAPER", include_zero, search, sort, order, limit)
    header = ["source", "asset", "free", "locked", "total",
              "retrieved_at", "freshness"]
    rows = []
    for sec in data["sections"]:
        meta = sec.get("meta") or {}
        for a in sec.get("assets", []):
            rows.append([_csv_text(sec["source"]), _csv_text(a["asset"]),
                         _csv_num(a["free"]), _csv_num(a["locked"]),
                         _csv_num(a["total"]),
                         _csv_text(meta.get("retrieved_at")),
                         _csv_text(meta.get("freshness"))])
    return (_csv_response_body(header, rows),
            f"alpha-portfolio-{_stamp()}.csv")


def positions_csv(include_zero: bool) -> tuple[bytes, str]:
    model = positions_view(include_zero=include_zero)
    header = ["symbol", "direction", "position_amt", "abs_quantity",
              "entry_price", "mark_price", "unrealized_pnl", "leverage",
              "liquidation_price", "margin_type", "update_time",
              "retrieved_at", "freshness"]
    rows = []
    meta = model.get("meta") or {}
    for p in (model.get("positions") or []) if model.get("ok") else []:
        rows.append([_csv_text(p["symbol"]), _csv_text(p["direction"]),
                     _csv_num(p["position_amt"]), _csv_num(p["abs_quantity"]),
                     _csv_num(p["entry_price"]), _csv_num(p["mark_price"]),
                     _csv_num(p["unrealized_pnl"]), _csv_num(p["leverage"]),
                     _csv_num(p["liquidation_price"]),
                     _csv_text(p["margin_type"]), _csv_text(p["update_time"]),
                     _csv_text(meta.get("retrieved_at")),
                     _csv_text(meta.get("freshness"))])
    return (_csv_response_body(header, rows),
            f"alpha-positions-{_stamp()}.csv")


def orders_csv() -> tuple[bytes, str]:
    model = orders_view()
    header = ["symbol", "side", "type", "status", "orig_qty", "executed_qty",
              "remaining_qty", "price", "stop_price", "reduce_only",
              "time", "update_time", "retrieved_at", "freshness"]
    rows = []
    meta = model.get("meta") or {}
    for o in (model.get("orders") or []) if model.get("ok") else []:
        rows.append([_csv_text(o["symbol"]), _csv_text(o["side"]),
                     _csv_text(o["type"]), _csv_text(o["status"]),
                     _csv_num(o["orig_qty"]), _csv_num(o["executed_qty"]),
                     _csv_num(o["remaining_qty"]), _csv_num(o["price"]),
                     _csv_num(o["stop_price"]),
                     _csv_text("true" if o["reduce_only"] else "false"),
                     _csv_text(o["time"]), _csv_text(o["update_time"]),
                     _csv_text(meta.get("retrieved_at")),
                     _csv_text(meta.get("freshness"))])
    return (_csv_response_body(header, rows),
            f"alpha-open-orders-{_stamp()}.csv")
