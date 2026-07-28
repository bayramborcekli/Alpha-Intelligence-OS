"""
Mission 1400.5 — Yönetici Çalışma Alanı servis katmanı (SALT-OKUNUR).

Tek uç nokta besler: GET /api/v1/executive/summary
- Performans şeridi: yalnızca DOĞRULANMIŞ değerler; bilinmeyen → null
  (UI "Veri Yok" / "—" gösterir). Tahmin, projeksiyon, uydurma yüzde YOK.
- Durum çubuğu: kaynak tazeliği / bütünlük / süreç durumundan türetilir.
- Borsa yazma yolu YOK; bu modül hiçbir POST/PUT/PATCH/DELETE üretmez.
- Para birimleri BİRLEŞTİRİLMEZ: "Toplam Portföy" Global SPOT hesabının
  USDT değeridir ve öyle etiketlenir. TR varlıkları dönüştürülmez.
"""

from __future__ import annotations

import dashboard_api as dapi
import ledger_api as la


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn_status(model: dict) -> str:
    """Kaynak modeli → Bağlı / Kısmi / Bağlantı Yok.

    Durum KANONİK dapi.connection_state'ten türetilir — bu katman kendi
    health/credential kontrolünü YAPMAZ (tek snapshot sözleşmesi)."""
    state = model.get("connection_state") or dapi.connection_state(model)
    if state == "HEALTHY":
        return "Bağlı"
    if state == "STALE":
        return "Kısmi"
    return "Bağlantı Yok"


def executive_summary(bot_is_running: bool, app_mode: str) -> dict:
    # Spot-only mimari: Global kaynak SPOT hesabıdır (futures kaldırıldı).
    gs = dapi.global_spot_account()
    ta = dapi.tr_account()
    integ = la.ledger_integrity()
    aud = la.audit_summary()

    # Mission 1400.6 — Risk Motoru: doğrulanmış deterministik skor
    risk_level = None
    risk_engine_status = "Bağlantı Yok"
    try:
        import risk_api as ra
        rs = ra.summary()
        if rs.get("ok"):
            risk_engine_status = "Bağlı"
            if rs.get("classification"):
                risk_level = (f"{rs['classification']} "
                              f"({rs['risk_score']}/100)")
    except Exception:
        risk_engine_status = "Bağlantı Yok"

    # ── Performans şeridi (doğrulanmış veri; yoksa null) ──────────────────
    # Spot hesabında marj/futures kavramı yok; toplam SPOT değeri kullanılır.
    portfolio_total = (gs.get("total_spot_value_usdt")
                       if gs.get("ok") else None)
    partial_valuation = gs.get("ok") and gs.get("valuation") == "PARTIAL"
    unrealized = None            # Spot'ta doğrulanmış uPnL kaynağı yok
    open_positions = None        # Futures kaldırıldı — pozisyon kavramı yok
    open_orders = None

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
            "portfolio_total_label": ("Global Spot (USDT, kısmi)"
                                      if partial_valuation
                                      else "Global Spot (USDT)"),
            "unrealized_pnl_usdt": unrealized,
            # Doğrulanmış gerçekleşmiş PnL kaynağı yok → null (asla tahmin yok)
            "realized_pnl_usdt": None,
            "daily_pnl_pct": None,        # doğrulanmış gün-başı tabanı yok
            "total_pnl_pct": None,        # doğrulanmış başlangıç tabanı yok
            "pnl_7d_usdt": None,          # doğrulanmış tarihsel seri yok
            "pnl_30d_usdt": None,
            "open_position_count": open_positions,
            "open_order_count": open_orders,
            # 1400.6: deterministik risk motoru skoru (doğrulanmış girdiler)
            "risk_level": risk_level,
        },
        "status_bar": {
            "binance_global": _conn_status(gs),
            "binance_tr": _conn_status(ta),
            "ledger": ledger_status,
            "audit": "Bağlı" if aud.get("ok") else "Bağlantı Yok",
            "risk_engine": risk_engine_status,
            "health": "Bağlı",            # uygulama bu yanıtı üretebildi
        },
    }
