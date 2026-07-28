"""Mission 1500.1 / Agent 06 — Intelligence Servis Katmanı.

UI ve API katmanlarının Intelligence verisini TEK noktadan tüketmesini
sağlar: mevcut salt-okunur servislerden (dashboard cache + Risk Engine)
veri toplar, normalize snapshot üretir, deterministik Intelligence
çekirdeğini (Agent 03), Risk Açıklama Motorunu (Agent 04) ve Tavsiye
Motorunu (Agent 05) çalıştırıp tek çıktı birleştirir.

Güvenlik sınırları:
- Kaynak servisler yalnızca OKUNUR; exchange istemcilerine yazma erişimi
  yoktur ve bu katman hiçbir imzalama/secret değeri görmez veya taşımaz.
- Ledger/audit geçmişi değiştirilmez; hiçbir dosyaya yazılmaz.
- Cache modeli mevcut güvenli modeldir: dashboard servislerinin kendi
  TTL'li cache'i kullanılır; bu katman ek cache tutmaz.
- Kaynak hataları sterilize mesaj (kod + Türkçe metin) olarak aktarılır;
  ham exception/secret asla dışarı yansıtılmaz.
- Partial veri gizlenmez: status PARTIAL/STALE/UNAVAILABLE olarak ve
  kaynak-bazlı tazelik listesiyle açıkça raporlanır.

Bağımlılıklar kurucuya enjekte edilebilir (testlerde mock).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import intelligence_api as icore
import recommendation_api as radv
import risk_explainer as rexp
from intelligence_models import DataFreshness, IntelligenceStatus, to_json

_FRESHNESS_MAP = {"FRESH": IntelligenceStatus.OK,
                  "STALE": IntelligenceStatus.STALE}


def _default_account():
    """Spot-only: Futures global_account kaldırıldı — boş model döner."""
    return {"ok": False, "error": {"code": "NOT_AVAILABLE",
            "message": "Futures hesabı kaldırıldı (Spot-only mimari)."}}


def _default_positions():
    """Spot-only: Futures pozisyon verisi kaldırıldı — boş model döner."""
    return {"ok": False, "error": {"code": "NOT_AVAILABLE",
            "message": "Futures pozisyonları kaldırıldı (Spot-only mimari)."}}


def _default_risk():
    import risk_api
    return risk_api.summary()


def _default_alerts():
    import risk_api
    return risk_api.alerts()


def _parse_iso(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None   # naive kabul edilmez


def _age(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _sterile_error(payload: dict | None) -> dict | None:
    """Kaynak hatasını yalnızca kod+mesaj olarak aktarır (secret yok)."""
    err = (payload or {}).get("error")
    if not isinstance(err, dict):
        return None
    return {"code": err.get("code"), "message": err.get("message")}


class IntelligenceService:
    """Intelligence verisi için tek tüketim noktası (salt-okunur)."""

    def __init__(self, account_provider=_default_account,
                 positions_provider=_default_positions,
                 risk_provider=_default_risk,
                 alerts_provider=_default_alerts):
        self._account = account_provider
        self._positions = positions_provider
        self._risk = risk_provider
        self._alerts = alerts_provider

    # ── Toplama ──────────────────────────────────────────────────────────
    @staticmethod
    def _safe(provider):
        """Sağlayıcı hatası izole edilir; ham exception yayılmaz.

        Gözlemlenebilirlik için sterilize hata kaydı üretilir (kod +
        sabit Türkçe mesaj) — exception metni ASLA dışarı taşınmaz.
        """
        try:
            out = provider()
            if isinstance(out, dict):
                return out
            return {"ok": False, "error": {
                "code": "PROVIDER_INVALID_RESPONSE",
                "message": "Kaynak beklenmeyen biçimde yanıt verdi."}}
        except Exception:
            return {"ok": False, "error": {
                "code": "PROVIDER_ERROR",
                "message": "Kaynak sağlayıcı hatası (ayrıntı gizlendi)."}}

    def _freshness(self, source: str, payload: dict | None) -> DataFreshness:
        meta = (payload or {}).get("meta") or {}
        if not payload or not payload.get("ok"):
            status = IntelligenceStatus.UNAVAILABLE
        elif "freshness" in meta:                      # dashboard tarzı meta
            status = _FRESHNESS_MAP.get(meta.get("freshness"),
                                        IntelligenceStatus.UNAVAILABLE)
        else:
            # Risk Engine tarzı yanıt: meta.freshness yok; geçerli ok
            # yanıtı OK sayılır (bayatlık, girdisi olan kaynaklarda
            # zaten kaynak-bazında raporlanır).
            status = IntelligenceStatus.OK
        observed = (_parse_iso(meta.get("retrieved_at"))
                    or _parse_iso((payload or {}).get("as_of")))
        return DataFreshness(status=status,
                             observed_at=observed,
                             age_seconds=_age(meta.get("age_seconds")),
                             source=source,
                             detail=(_sterile_error(payload) or {}
                                     ).get("message"))

    def get_snapshot(self) -> dict:
        """Normalize edilmiş, tek seferde toplanmış veri anlık görüntüsü."""
        ga = self._safe(self._account)
        gp = self._safe(self._positions)
        rs = self._safe(self._risk)
        al = self._safe(self._alerts)

        account = (ga.get("account") if ga and ga.get("ok") else None)
        positions = (gp.get("positions") if gp and gp.get("ok") else None)
        risk = rs if rs and rs.get("ok") else None
        alerts = (al.get("alerts") if al and al.get("ok") else None)
        freshness = [self._freshness("global_account", ga),
                     self._freshness("global_positions", gp),
                     self._freshness("risk_engine", rs),
                     self._freshness("risk_engine_alerts", al)]
        errors = {src: e for src, e in (
            ("global_account", _sterile_error(ga)),
            ("global_positions", _sterile_error(gp)),
            ("risk_engine", _sterile_error(rs)),
            ("risk_engine_alerts", _sterile_error(al))) if e}
        return {"account": account, "positions": positions,
                "risk_summary": risk, "alerts": alerts,
                "freshness": freshness, "errors": errors}

    # ── Birleşik çıktılar ───────────────────────────────────────────────
    def get_summary(self, generated_at: datetime | None = None) -> dict:
        """API/UI için TEK birleşik çıktı (JSON-hazır dict)."""
        if generated_at is None:
            generated_at = datetime.now(timezone.utc)
        snap = self.get_snapshot()
        summary = icore.build_intelligence_summary(
            account=snap["account"], positions=snap["positions"],
            risk_summary=snap["risk_summary"],
            freshness_list=snap["freshness"], generated_at=generated_at)
        explanations = rexp.explain_risk(snap["risk_summary"])
        recs = radv.build_recommendations(
            account=snap["account"], positions=snap["positions"],
            risk_summary=snap["risk_summary"], alerts=snap["alerts"],
            freshness_list=snap["freshness"], generated_at=generated_at)
        import json as _json
        payload = _json.loads(to_json(summary))
        payload["risk_explanations"] = [i.to_dict() for i in explanations]
        payload["recommendations"] = recs["recommendations"]
        payload["source_errors"] = snap["errors"]
        payload["partial"] = payload["status"] != "OK"
        payload["ok"] = True
        payload["read_only"] = True
        return payload

    def get_insights(self, generated_at: datetime | None = None,
                     summary: dict | None = None) -> list:
        """`summary` verilirse yeniden hesap yapılmaz (tek çağrı paylaşımı)."""
        return (summary or self.get_summary(generated_at))["insights"]

    def get_recommendations(self, generated_at: datetime | None = None,
                            summary: dict | None = None) -> list:
        """`summary` verilirse yeniden hesap yapılmaz (tek çağrı paylaşımı)."""
        return (summary or self.get_summary(generated_at))["recommendations"]

    def get_status(self) -> dict:
        """Hafif durum özeti (çekirdek çalıştırılmadan)."""
        snap = self.get_snapshot()
        fresh = [{"source": f.source, "status": f.status.value,
                  "age_seconds": (str(f.age_seconds)
                                  if f.age_seconds is not None else None)}
                 for f in snap["freshness"]]
        # get_summary() ile AYNI kural: tüm ana kaynaklar yok →
        # UNAVAILABLE; herhangi biri yok → PARTIAL; bayat → STALE.
        core = {"account": snap["account"], "positions": snap["positions"],
                "risk": snap["risk_summary"]}
        missing = [k for k, v in core.items()
                   if v is None or (k != "positions" and not v)]
        stale = any(f.status in (IntelligenceStatus.STALE,
                                 IntelligenceStatus.UNAVAILABLE)
                    for f in snap["freshness"])
        if len(missing) == len(core):
            overall = IntelligenceStatus.UNAVAILABLE
        elif missing:
            overall = IntelligenceStatus.PARTIAL
        elif stale:
            overall = IntelligenceStatus.STALE
        else:
            overall = IntelligenceStatus.OK
        return {"ok": True, "read_only": True, "advisory_only": True,
                "status": overall.value, "partial":
                overall is not IntelligenceStatus.OK,
                "sources": fresh, "errors": snap["errors"]}
