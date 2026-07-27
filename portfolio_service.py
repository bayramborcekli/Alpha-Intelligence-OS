"""Mission 1700 — Portfolio Intelligence servis katmanı (Agent 03).

Mevcut SALT-OKUNUR sağlayıcılardan portföy girdilerini toplar, sterile
biçimde normalize eder ve TÜM hesaplamayı ``portfolio_intelligence
.analyze_portfolio`` çekirdeğine devreder. Servis hiçbir portföy
matematiği yapmaz (allocation/exposure/PnL/health burada YOK).

Sözleşmeler (Agent 01 mimarisi — değiştirilemez):
- Workspace/Timeline yazımı yok; ``append_snapshot`` çağrısı yok;
  snapshot gömme bu agent'ta bilinçli olarak YOK (ileri karar).
- Exchange/emir/execution yok; Risk Engine salt-okunur ve otoritedir
  (limitler okunur, asla tanımlanmaz/kalıcılaştırılmaz).
- Flask/request bağımlılığı yok; global değişken durum yok; thread ve
  scheduler yok; duvar saati okunmaz (``generated_at`` enjekte edilir).
- Sağlayıcı exception'ları asla dışarı sızmaz: sterile ``unavailable``
  durumuna çevrilir; bilinmeyen değer null kalır, asla 0 uydurulmaz.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Mapping

import portfolio_intelligence

# Sağlayıcı sınırları (Agent 01 §5): equity = nakit/bakiye + PnL
# performansı; positions = pozisyonlar + mark değerleme; risk = Risk
# Engine limitleri + drawdown görünümü.
PROVIDER_NAMES = ("equity", "positions", "risk")

FRESHNESS_STATES = ("fresh", "stale")

# Sterile kaynak kodları
CODE_PROVIDER_FAILED = "PROVIDER_FAILED"
CODE_INVALID_RESULT = "INVALID_PROVIDER_RESULT"
CODE_UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"

_EQUITY_FIELDS = ("nav_usdt", "cash_usdt", "realized_pnl",
                  "unrealized_pnl", "total_fees")
_THRESHOLD_FIELDS = ("max_net_exposure_pct", "max_drawdown_pct",
                     "max_concentration_pct")


# ── Sağlayıcı toplama (sterile) ──────────────────────────────────────

def _collect(provider: Callable[[], Any]) -> dict[str, Any]:
    """Tek sağlayıcıyı sterile biçimde çalıştırır; exception sızdırmaz."""
    try:
        result = provider()
    except BaseException:
        return {"available": False, "freshness": None,
                "code": CODE_PROVIDER_FAILED, "data": None}
    if (not isinstance(result, dict)
            or result.get("freshness") not in FRESHNESS_STATES
            or "data" not in result):
        return {"available": False, "freshness": None,
                "code": CODE_INVALID_RESULT, "data": None}
    return {"available": True, "freshness": result["freshness"],
            "code": None, "data": result["data"]}


def _core_sources(collected: Mapping[str, dict]) -> dict[str, str]:
    return {name: (meta["freshness"] if meta["available"]
                   else "unavailable")
            for name, meta in collected.items()}


def _service_sources(collected: Mapping[str, dict]) -> dict[str, dict]:
    """Zarfa yazılan zengin ama sterile kaynak meta verisi."""
    out: dict[str, dict] = {}
    for name in sorted(collected):
        meta = collected[name]
        out[name] = {
            "status": "ok" if meta["available"] else "failed",
            "freshness": meta["freshness"] if meta["available"]
            else "unavailable",
            "available": meta["available"],
            "code": meta["code"],
        }
    return out


# ── Girdi çevirisi (yalnız taşıma — hesap YOK) ───────────────────────

def _translate(collected: Mapping[str, dict],
               generated_at: str | None) -> dict[str, Any]:
    equity_data = collected["equity"]["data"]
    equity = {}
    if isinstance(equity_data, dict):
        equity = {k: equity_data.get(k) for k in _EQUITY_FIELDS}

    positions_data = collected["positions"]["data"]
    positions = positions_data if isinstance(positions_data, list) else []

    risk_data = collected["risk"]["data"]
    risk: dict[str, Any] = {}
    if isinstance(risk_data, dict):
        thresholds = risk_data.get("thresholds")
        risk = {
            "drawdown_pct": risk_data.get("drawdown_pct"),
            "thresholds": {k: thresholds.get(k) for k in _THRESHOLD_FIELDS}
            if isinstance(thresholds, dict) else {},
        }

    return {
        "generated_at": generated_at,
        "sources": _core_sources(collected),
        "equity": equity,
        "positions": positions,
        "risk": risk,
    }


def _degrade(collected: dict[str, dict], name: str) -> None:
    collected[name] = {"available": False, "freshness": None,
                       "code": CODE_INVALID_RESULT, "data": None}


# ── Kamu sözleşmesi ──────────────────────────────────────────────────

def get_portfolio_analysis(providers: Mapping[str, Callable[[], Any]],
                           generated_at: str | None = None
                           ) -> dict[str, Any]:
    """Sağlayıcıları toplar, çevirir ve çekirdeğe devreder.

    ``providers``: ``{"equity"|"positions"|"risk": callable}``. Her
    callable ``{"freshness": "fresh"|"stale", "data": ...}`` döndürür;
    exception fırlatan/bozuk sağlayıcı sterile ``unavailable`` olur.
    Dönüş: çekirdek zarfı; yalnız ``sources`` servis meta verisiyle
    zenginleştirilir (başka hiçbir alan değiştirilmez).
    """
    if not isinstance(providers, Mapping):
        raise ValueError(portfolio_intelligence.ERROR_INVALID_INPUT)
    for name in providers:
        if name not in PROVIDER_NAMES:
            raise ValueError(CODE_UNKNOWN_PROVIDER)

    collected = {}
    for name in PROVIDER_NAMES:  # sabit sıra — çağrı sırası etkisiz
        provider = providers.get(name)
        collected[name] = (_collect(provider) if callable(provider)
                           else {"available": False, "freshness": None,
                                 "code": CODE_INVALID_RESULT,
                                 "data": None})

    # Bozuk sağlayıcı VERİSİ çekirdek doğrulamasına takılırsa yalnız
    # SUÇLU sağlayıcı sterile biçimde düşürülür: her sağlayıcı tek
    # başına (diğerleri unavailable sayılarak) doğrulanır.
    for name in PROVIDER_NAMES:
        if not collected[name]["available"]:
            continue
        isolated = {other: (collected[other] if other == name
                            else {"available": False, "freshness": None,
                                  "code": None, "data": None})
                    for other in PROVIDER_NAMES}
        try:
            portfolio_intelligence.analyze_portfolio(
                _translate(isolated, None))
        except ValueError:
            _degrade(collected, name)

    envelope = portfolio_intelligence.analyze_portfolio(
        _translate(collected, generated_at))
    envelope["sources"] = _service_sources(collected)
    return envelope


class PortfolioService:
    """İnce OO sarmalayıcı; durum tutmaz (yalnız enjekte bağımlılık)."""

    def __init__(self, providers: Mapping[str, Callable[[], Any]]):
        self._providers = dict(providers)

    def get_analysis(self, generated_at: str | None = None
                     ) -> dict[str, Any]:
        return get_portfolio_analysis(self._providers, generated_at)


# ── Gerçek sağlayıcı çeviricileri (saf — canlı çağrı içermez) ────────

def map_account_to_equity(account: Any) -> dict[str, Any]:
    """Dashboard hesap görünümü → equity girdisi.

    NAV = margin balance; nakit = available balance. Ledger bazlı
    realized_pnl/total_fees bu görünümde YOKTUR → null kalır (dürüst).
    """
    if not isinstance(account, dict):
        return {k: None for k in _EQUITY_FIELDS}
    return {
        "nav_usdt": account.get("usdt_margin_balance"),
        "cash_usdt": account.get("usdt_available_balance"),
        "realized_pnl": account.get("realized_pnl"),
        "unrealized_pnl": account.get("unrealized_pnl"),
        "total_fees": account.get("total_fees"),
    }


def map_positions(raw_positions: Any) -> list[dict[str, Any]]:
    """Dashboard pozisyon görünümü → çekirdek pozisyon girdileri.

    FLAT/sıfır miktar atlanır; miktar mutlak değerdir (yön ``side``).
    """
    out: list[dict[str, Any]] = []
    if not isinstance(raw_positions, list):
        return out
    for row in raw_positions:
        if not isinstance(row, dict):
            continue
        side = row.get("direction")
        if side not in ("LONG", "SHORT"):
            continue
        amt = row.get("position_amt")
        if amt is None:
            continue
        quantity = str(amt).strip().lstrip("-")
        if not quantity or Decimal(quantity) == 0:
            continue
        out.append({
            "symbol": row.get("symbol"),
            "side": side,
            "quantity": quantity,
            "entry_price": row.get("entry_price"),
            "mark_price": row.get("mark_price"),
            "leverage": row.get("leverage"),
        })
    return out


def map_risk_view(thresholds: Any, drawdown_pct: Any) -> dict[str, Any]:
    """Risk Engine eşikleri + güncel drawdown → risk girdisi.

    Eşleme (Risk Engine otoritesi — yeniden tanım YOK):
    - max_net_exposure_pct  ← HIGH_EXPOSURE_PERCENT
    - max_drawdown_pct      ← |DRAWDOWN_WARN_PERCENT|
    - max_concentration_pct ← POSITION_CRITICAL_PERCENT
    """
    t = thresholds if isinstance(thresholds, dict) else {}
    warn = t.get("DRAWDOWN_WARN_PERCENT")
    max_dd = None
    if warn is not None:
        max_dd = str(warn).strip().lstrip("-") or None
    return {
        "drawdown_pct": drawdown_pct,
        "thresholds": {
            "max_net_exposure_pct": t.get("HIGH_EXPOSURE_PERCENT"),
            "max_drawdown_pct": max_dd,
            "max_concentration_pct": t.get("POSITION_CRITICAL_PERCENT"),
        },
    }


def build_default_providers() -> dict[str, Callable[[], Any]]:
    """Mevcut salt-okunur yüzeylerden varsayılan sağlayıcı seti.

    İçe aktarmalar tembeldir (çağrı anında); bu modülün import'u hiçbir
    canlı sisteme dokunmaz. Tazelik: sağlayıcı sonucu anlık görünümdür
    → ``fresh``; hata sterile ``unavailable`` olur (``_collect``).
    """
    def equity_provider() -> dict[str, Any]:
        import intelligence_service
        snapshot = intelligence_service.IntelligenceService().get_snapshot()
        return {"freshness": "fresh",
                "data": map_account_to_equity(snapshot.get("account"))}

    def positions_provider() -> dict[str, Any]:
        import intelligence_service
        snapshot = intelligence_service.IntelligenceService().get_snapshot()
        return {"freshness": "fresh",
                "data": map_positions(snapshot.get("positions"))}

    def risk_provider() -> dict[str, Any]:
        import risk_api
        # persist=False: salt-okunur görünüm — Risk Engine'in günlük
        # snapshot'ı portföy GET isteğiyle ASLA yazılmaz.
        summary = risk_api.summary(persist=False)
        return {"freshness": "fresh",
                "data": map_risk_view(
                    risk_api.thresholds(),
                    summary.get("daily_drawdown_pct")
                    if isinstance(summary, dict) else None)}

    return {"equity": equity_provider,
            "positions": positions_provider,
            "risk": risk_provider}
