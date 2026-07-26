"""
Mission 1400.5 — Yönetici Çalışma Alanı servis katmanı (SALT-OKUNUR).

Tek uç nokta besler: GET /api/v1/executive/summary
- Performans şeridi: yalnızca DOĞRULANMIŞ değerler; bilinmeyen → null
  (UI "Veri Yok" / "—" gösterir). Tahmin, projeksiyon, uydurma yüzde YOK.
- Durum çubuğu: kaynak tazeliği / bütünlük / süreç durumundan türetilir.
- Borsa yazma yolu YOK; bu modül hiçbir POST/PUT/PATCH/DELETE üretmez.
- Para birimleri BİRLEŞTİRİLMEZ: "Toplam Portföy" Global Futures cüzdanının
  USDT marj bakiyesidir ve öyle etiketlenir. TR varlıkları dönüştürülmez.
"""

from __future__ import annotations

import dashboard_api as dapi
import portfolio_api as pf
import ledger_api as la


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn_status(model: dict) -> str:
    """Kaynak modeli → Bağlı / Kısmi / Bağlantı Yok (renkten bağımsız metin)."""
    if not model.get("ok"):
        return "Bağlantı Yok"
    fresh = ((model.get("meta") or {}).get("freshness") or "").upper()
    if fresh == "STALE":
        return "Kısmi"
    return "Bağlı"


def executive_summary(bot_is_running: bool, app_mode: str) -> dict:
    ga = dapi.global_account()
    gp = pf.positions_view()
    go = pf.orders_view()
    ta = dapi.tr_account()
    integ = la.ledger_integrity()
    aud = la.audit_summary()

    acc = ga.get("account") or {} if ga.get("ok") else {}

    # ── Performans şeridi (doğrulanmış veri; yoksa null) ──────────────────
    portfolio_total = acc.get("usdt_margin_balance") if ga.get("ok") else None
    unrealized = None
    if gp.get("ok"):
        unrealized = (gp.get("summary") or {}).get("total_unrealized_pnl")
    elif ga.get("ok"):
        unrealized = acc.get("unrealized_pnl")
    open_positions = ((gp.get("summary") or {}).get("active_count")
                      if gp.get("ok") else None)
    open_orders = ((go.get("summary") or {}).get("open_count")
                   if go.get("ok") else None)

    ledger_status = {"PASS": "Bağlı", "PARTIAL": "Kısmi"}.get(
        integ.get("status"), "Bağlantı Yok")

    return {
        "ok": True,
        "mode": app_mode,
        "live_execution": False,          # canlı emir: her zaman KAPALI
        "as_of": _now_iso(),
        "performance": {
            # Tek para birimi; çapraz kur birleştirme YAPILMAZ.
            "portfolio_total_usdt": portfolio_total,
            "portfolio_total_label": "Global Futures (USDT)",
            "unrealized_pnl_usdt": unrealized,
            # Doğrulanmış gerçekleşmiş PnL kaynağı yok → null (asla tahmin yok)
            "realized_pnl_usdt": None,
            "daily_pnl_pct": None,        # doğrulanmış gün-başı tabanı yok
            "total_pnl_pct": None,        # doğrulanmış başlangıç tabanı yok
            "pnl_7d_usdt": None,          # doğrulanmış tarihsel seri yok
            "pnl_30d_usdt": None,
            "open_position_count": open_positions,
            "open_order_count": open_orders,
            "risk_level": None,           # doğrulanmış risk metriği yok
        },
        "status_bar": {
            "binance_global": _conn_status(ga),
            "binance_futures": _conn_status(gp),
            "binance_tr": _conn_status(ta),
            "ledger": ledger_status,
            "audit": "Bağlı" if aud.get("ok") else "Bağlantı Yok",
            "risk_engine": "Bağlı" if bot_is_running else "Bağlantı Yok",
            "health": "Bağlı",            # uygulama bu yanıtı üretebildi
        },
    }
