"""Mission 1700 — Portfolio Intelligence çekirdeği (Agent 02).

Portföy DURUMUNU yorumlayan saf (pure), deterministik analiz katmanı.
Girdiler enjekte edilir (I/O yok, saat okuma yok, sağlayıcı yok —
Agent 03 kapsamı); aynı girdi her zaman bayt-özdeş çıktı üretir.

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- SALT-OKUNUR ve ADVISORY-ONLY: emir/execution/exchange kavramı yoktur.
- Workspace/Timeline'a yazmaz; hiçbir proje modülünü import etmez.
- Para matematiği yalnız Decimal; float girdisi REDDEDİLİR.
- Bilinmeyen değer null kalır; asla 0 uydurulmaz.
- Risk eşikleri Risk Engine'den ENJEKTE edilir; burada tanımlanmaz,
  Risk Engine otoritesi değiştirilmez.
- Piyasa istihbaratı hesaplanmaz (Intelligence Engine kapsamı).

Hata modeli: bozuk girdi sterile ``ValueError`` kodu üretir
(FLOAT_REJECTED / INVALID_INPUT); exception metni veri taşımaz.
"""

from __future__ import annotations

from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any

ANALYSIS_VERSION = 1

# Sterile hata kodları
ERROR_FLOAT_REJECTED = "FLOAT_REJECTED"
ERROR_INVALID_INPUT = "INVALID_INPUT"

# Durumlar
STATUS_OK = "OK"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNAVAILABLE = "UNAVAILABLE"

# Limit ihlal kodları (sterile)
BREACH_NET_EXPOSURE = "LIMIT_NET_EXPOSURE"
BREACH_DRAWDOWN = "LIMIT_DRAWDOWN"
BREACH_CONCENTRATION = "LIMIT_CONCENTRATION"

# Sağlık skoru bileşenleri: (kod, kullanılan utilizasyon anahtarı, ağırlık)
_HEALTH_COMPONENTS = (
    ("EXPOSURE", "net_exposure", Decimal("0.4")),
    ("DRAWDOWN", "drawdown", Decimal("0.4")),
    ("CONCENTRATION", "concentration", Decimal("0.2")),
)

_Q_AMOUNT = Decimal("0.00000001")   # tutar hassasiyeti
_Q_PCT = Decimal("0.01")            # yüzde/skor hassasiyeti

_SIDES = ("LONG", "SHORT")

# Sayısal alan sınırı: aşırı büyüklükler (uç üsler) reddedilir; böylece
# quantize/aritmetik hiçbir girdide taşma üretemez (sterile sözleşme).
_MAX_ABS = Decimal("1E+18")
_SOURCE_STATES = ("fresh", "stale", "unavailable")


# ── Decimal yardımcıları ─────────────────────────────────────────────

def _dec(value: Any, field: str) -> Decimal | None:
    """Girdiyi Decimal'e çevirir; float YASAK; bilinmeyen → None."""
    if value is None:
        return None
    if isinstance(value, float):
        raise ValueError(ERROR_FLOAT_REJECTED)
    if isinstance(value, bool):
        raise ValueError(ERROR_INVALID_INPUT)
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, str):
        try:
            candidate = Decimal(value.strip())
        except (InvalidOperation, AttributeError):
            raise ValueError(ERROR_INVALID_INPUT)
    else:
        raise ValueError(ERROR_INVALID_INPUT)
    if not candidate.is_finite() or abs(candidate) >= _MAX_ABS:
        raise ValueError(ERROR_INVALID_INPUT)
    return candidate


def _amount(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(_Q_AMOUNT), "f")


def _pct(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(_Q_PCT), "f")


def _ratio_pct(part: Decimal | None, whole: Decimal | None) -> Decimal | None:
    if part is None or whole is None or whole <= 0:
        return None
    return (part / whole) * Decimal("100")


# ── Girdi normalizasyonu ─────────────────────────────────────────────

def _norm_equity(raw: Any) -> dict[str, Decimal | None]:
    raw = raw if isinstance(raw, dict) else {}
    return {k: _dec(raw.get(k), k) for k in
            ("nav_usdt", "cash_usdt", "realized_pnl",
             "unrealized_pnl", "total_fees")}


def _norm_position(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("symbol"), str) \
            or not raw.get("symbol").strip():
        raise ValueError(ERROR_INVALID_INPUT)
    side = raw.get("side")
    if side not in _SIDES:
        raise ValueError(ERROR_INVALID_INPUT)
    qty = _dec(raw.get("quantity"), "quantity")
    if qty is None or qty <= 0:
        raise ValueError(ERROR_INVALID_INPUT)
    return {
        "symbol": raw["symbol"].strip(),
        "side": side,
        "quantity": qty,
        "entry_price": _dec(raw.get("entry_price"), "entry_price"),
        "mark_price": _dec(raw.get("mark_price"), "mark_price"),
        "leverage": _dec(raw.get("leverage"), "leverage"),
    }


def _norm_risk(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    thresholds = raw.get("thresholds")
    thresholds = thresholds if isinstance(thresholds, dict) else {}
    return {
        "drawdown_pct": _dec(raw.get("drawdown_pct"), "drawdown_pct"),
        "thresholds": {k: _dec(thresholds.get(k), k) for k in
                       ("max_net_exposure_pct", "max_drawdown_pct",
                        "max_concentration_pct")},
    }


def _norm_sources(raw: Any) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, str] = {}
    for key in sorted(raw):
        state = raw[key]
        if not isinstance(key, str) or state not in _SOURCE_STATES:
            raise ValueError(ERROR_INVALID_INPUT)
        out[key] = state
    return out


# ── Analiz blokları ──────────────────────────────────────────────────

def _analyze_positions(positions: list[dict], nav: Decimal | None):
    """Pozisyon görünümü + bilinen brüt/net/long/short toplamları."""
    view = []
    gross = Decimal("0")
    net = Decimal("0")
    long_total = Decimal("0")
    short_total = Decimal("0")
    known_notionals: list[tuple[str, Decimal]] = []
    unknown = 0
    for p in sorted(positions, key=lambda x: (x["symbol"], x["side"])):
        mark = p["mark_price"]
        notional = (p["quantity"] * mark) if mark is not None else None
        upnl = None
        if mark is not None and p["entry_price"] is not None:
            diff = (mark - p["entry_price"]) if p["side"] == "LONG" \
                else (p["entry_price"] - mark)
            upnl = diff * p["quantity"]
        if notional is None:
            unknown += 1
        else:
            gross += notional
            if p["side"] == "LONG":
                net += notional
                long_total += notional
            else:
                net -= notional
                short_total += notional
            known_notionals.append((p["symbol"], notional))
        view.append({
            "symbol": p["symbol"],
            "side": p["side"],
            "quantity": _amount(p["quantity"]),
            "entry_price": _amount(p["entry_price"]),
            "mark_price": _amount(mark),
            "leverage": _amount(p["leverage"]),
            "notional": _amount(notional),
            "unrealized_pnl": _amount(upnl),
            "weight_pct": _pct(_ratio_pct(notional, nav)),
        })
    totals = {"gross": gross, "net": net, "long": long_total,
              "short": short_total} if positions and unknown < len(positions) \
        else ({"gross": Decimal("0"), "net": Decimal("0"),
               "long": Decimal("0"), "short": Decimal("0")}
              if not positions else None)
    return view, totals, known_notionals, unknown


def _concentration(known: list[tuple[str, Decimal]], gross: Decimal | None):
    """HHI, en yüksek pay ve etkin pozisyon sayısı (çeşitlendirme)."""
    if gross is None or gross <= 0 or not known:
        return {"hhi": None, "top_symbol": None, "top_share_pct": None,
                "effective_positions": None}, None
    by_symbol: dict[str, Decimal] = {}
    for symbol, notional in known:
        by_symbol[symbol] = by_symbol.get(symbol, Decimal("0")) + notional
    shares = {s: v / gross for s, v in sorted(by_symbol.items())}
    hhi = sum((v * v for v in shares.values()), Decimal("0"))
    top_symbol = min((s for s, v in shares.items()
                      if v == max(shares.values())))
    top_share = shares[top_symbol] * Decimal("100")
    effective = (Decimal("1") / hhi) if hhi > 0 else None
    return {
        "hhi": _pct(hhi * Decimal("100")),
        "top_symbol": top_symbol,
        "top_share_pct": _pct(top_share),
        "effective_positions": _pct(effective),
    }, top_share


def _utilization(value: Decimal | None, limit: Decimal | None) -> Decimal | None:
    if value is None or limit is None or limit <= 0:
        return None
    return (abs(value) / limit) * Decimal("100")


def _health(utils: dict[str, Decimal | None]):
    """Bileşen bazlı 0-100 sağlık skoru; bilinmeyen bileşen dışlanır."""
    components = []
    weighted = Decimal("0")
    weight_sum = Decimal("0")
    for code, key, weight in _HEALTH_COMPONENTS:
        util = utils.get(key)
        if util is None:
            components.append({"code": code, "score": None,
                               "weight": _pct(weight * Decimal("100"))})
            continue
        score = Decimal("100") - util
        score = Decimal("0") if score < 0 else (
            Decimal("100") if score > 100 else score)
        components.append({"code": code, "score": _pct(score),
                           "weight": _pct(weight * Decimal("100"))})
        weighted += score * weight
        weight_sum += weight
    score = (weighted / weight_sum) if weight_sum > 0 else None
    return {"portfolio_health_score": _pct(score), "components": components}


# ── Ana giriş noktası ────────────────────────────────────────────────

def analyze(inputs: Any) -> dict[str, Any]:
    """Normalize edilmiş portföy analizi zarfı üretir (saf fonksiyon).

    Her Decimal taşması/aritmetik sınır durumu sterile
    ``ValueError(INVALID_INPUT)`` olarak yüzeye çıkar.
    """
    try:
        return _analyze(inputs)
    except DecimalException:
        raise ValueError(ERROR_INVALID_INPUT)


def analyze_portfolio(inputs: Any) -> dict[str, Any]:
    """Resmî sözleşme adı (Mission 1700 §4) — ``analyze`` ile özdeş."""
    return analyze(inputs)


def _analyze(inputs: Any) -> dict[str, Any]:
    if not isinstance(inputs, dict):
        raise ValueError(ERROR_INVALID_INPUT)
    generated_at = inputs.get("generated_at")
    if generated_at is not None and not isinstance(generated_at, str):
        raise ValueError(ERROR_INVALID_INPUT)
    sources = _norm_sources(inputs.get("sources"))
    equity = _norm_equity(inputs.get("equity"))
    raw_positions = inputs.get("positions")
    if raw_positions is None:
        raw_positions = []
    if not isinstance(raw_positions, list):
        raise ValueError(ERROR_INVALID_INPUT)
    positions = [_norm_position(p) for p in raw_positions]
    risk = _norm_risk(inputs.get("risk"))

    nav = equity["nav_usdt"]
    view, totals, known_notionals, unknown_count = \
        _analyze_positions(positions, nav)

    # Exposure (yalnız bilinen notional'lar; hiçbiri bilinmiyorsa null)
    if totals is None:
        exposure = {k: None for k in
                    ("gross", "net", "long", "short",
                     "gross_pct", "net_pct")}
        net_pct = None
        gross_for_conc = None
    else:
        net_pct = _ratio_pct(totals["net"], nav)
        exposure = {
            "gross": _amount(totals["gross"]),
            "net": _amount(totals["net"]),
            "long": _amount(totals["long"]),
            "short": _amount(totals["short"]),
            "gross_pct": _pct(_ratio_pct(totals["gross"], nav)),
            "net_pct": _pct(net_pct),
        }
        gross_for_conc = totals["gross"]
    exposure["unknown_positions"] = unknown_count

    concentration, top_share = _concentration(known_notionals, gross_for_conc)

    # Allocation
    cash_weight = _ratio_pct(equity["cash_usdt"], nav)
    known_weight = _ratio_pct(gross_for_conc, nav) if gross_for_conc \
        is not None else (Decimal("0") if nav and nav > 0 and not positions
                          else None)
    unalloc = None
    if nav is not None and nav > 0 and cash_weight is not None \
            and known_weight is not None and unknown_count == 0:
        unalloc = Decimal("100") - cash_weight - known_weight
    allocation = {
        "assets": [{"symbol": p["symbol"], "notional": p["notional"],
                    "weight_pct": p["weight_pct"]} for p in view],
        "cash_weight_pct": _pct(cash_weight),
        "unallocated_or_unknown_pct": _pct(unalloc),
    }

    # Risk utilizasyonu (eşikler Risk Engine'den enjekte — otorite orada)
    thresholds = risk["thresholds"]
    utils = {
        "net_exposure": _utilization(net_pct,
                                     thresholds["max_net_exposure_pct"]),
        "drawdown": _utilization(risk["drawdown_pct"],
                                 thresholds["max_drawdown_pct"]),
        "concentration": _utilization(top_share,
                                      thresholds["max_concentration_pct"]),
    }
    breaches = sorted(code for code, key in
                      ((BREACH_NET_EXPOSURE, "net_exposure"),
                       (BREACH_DRAWDOWN, "drawdown"),
                       (BREACH_CONCENTRATION, "concentration"))
                      if utils[key] is not None and utils[key] > 100)
    risk_utilization = {
        "net_exposure_util_pct": _pct(utils["net_exposure"]),
        "drawdown_util_pct": _pct(utils["drawdown"]),
        "concentration_util_pct": _pct(utils["concentration"]),
        "limits_breached": breaches,
    }

    performance = {
        "realized_pnl": _amount(equity["realized_pnl"]),
        "unrealized_pnl": _amount(equity["unrealized_pnl"]),
        "total_fees": _amount(equity["total_fees"]),
        "drawdown_pct": _pct(risk["drawdown_pct"]),
        "forecast": None,  # tahmin yok — mevcut sözleşme
    }

    # Durum: NAV yoksa UNAVAILABLE; eksik/bayat veri PARTIAL; aksi OK
    if nav is None:
        status = STATUS_UNAVAILABLE
    elif (unknown_count > 0
          or any(v != "fresh" for v in sources.values())
          or any(equity[k] is None for k in
                 ("cash_usdt", "realized_pnl", "unrealized_pnl"))
          or risk["drawdown_pct"] is None
          or any(v is None for v in thresholds.values())):
        status = STATUS_PARTIAL
    else:
        status = STATUS_OK

    return {
        "ok": True,
        "read_only": True,
        "advisory_only": True,
        "analysis_version": ANALYSIS_VERSION,
        "status": status,
        "generated_at": generated_at,
        "sources": sources,
        "portfolio": {
            "equity": {
                "nav_usdt": _amount(nav),
                "cash_usdt": _amount(equity["cash_usdt"]),
                "realized_pnl": _amount(equity["realized_pnl"]),
                "unrealized_pnl": _amount(equity["unrealized_pnl"]),
                "total_fees": _amount(equity["total_fees"]),
            },
            "positions": view,
            "allocation": allocation,
            "exposure": exposure,
            "concentration": concentration,
            "performance": performance,
            "risk_utilization": risk_utilization,
            "health": _health(utils),
        },
    }
