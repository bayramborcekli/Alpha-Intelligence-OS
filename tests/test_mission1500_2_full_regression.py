"""Mission 1500.2 / Agent 08 — Uçtan uca tam regresyon doğrulaması.

Timeline → Service → API → UI → Export zincirini boş, tek-kayıt,
çok-kayıt ve eski/kısmi şema durumlarında birlikte doğrular.
Yeni özellik yoktur; yalnızca bütünleşik davranış kanıtlanır.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import intelligence_timeline as tl

PASSWORD = "Full-Regression-1500!"


def _snap(hour, **over):
    base = {
        "generated_at": f"2026-07-26T{hour}:00:00+00:00",
        "status": "OK",
        "partial": False,
        "freshness": [{"source": "account", "status": "OK"}],
        "insights": [{"code": "PORTFOLIO_OK", "confidence": "HIGH"}],
        "recommendations": [{"code": "NO_ACTION_NEEDED", "priority": 99,
                             "confidence": "MEDIUM"}],
        "warnings": [],
        "portfolio_summary": {"total_value": Decimal("1234.56789012")},
        "risk_summary": {"score": 71, "status": "SAGLIKLI",
                         "components": []},
        "risk_explanations": [],
        "advisory_only": True,
    }
    base.update(over)
    return base


@pytest.fixture()
def env(tmp_path, monkeypatch):
    hist = tmp_path / "history.jsonl"
    monkeypatch.setenv("ALPHA_INTELLIGENCE_HISTORY_PATH", str(hist))
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "owner")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH",
                       generate_password_hash(PASSWORD))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-regr-08")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", str(tmp_path / "att.json"))
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    c = flask_app.app.test_client()
    r = c.post("/api/v1/auth/login",
               json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 200
    return c, hist


READ_EPS = ("/api/workspace/timeline", "/api/workspace/recommendations",
            "/api/workspace/risk-evolution", "/api/workspace/search")


# ── 3. Boş geçmiş ────────────────────────────────────────────────────

def test_empty_history_honest_zero_free(env):
    c, hist = env
    assert not hist.exists()
    for ep in READ_EPS:
        body = c.get(ep).get_json()
        assert body["ok"] is True, ep
    assert c.get("/api/workspace/timeline").get_json() == \
        c.get("/api/v1/workspace/timeline").get_json()
    tljs = c.get("/api/workspace/timeline").get_json()
    assert tljs["total"] == 0 and tljs["entries"] == []
    assert c.get("/api/workspace/recommendations").get_json()["items"] == []
    risk = c.get("/api/workspace/risk-evolution").get_json()
    assert risk["series"] == [] and risk["forecast"] is None
    assert c.get("/api/workspace/snapshot/1").status_code == 404
    # Export boş durumda da dürüst çalışır
    csvt = c.get("/api/workspace/export/timeline?format=csv"
                 ).data.decode("utf-8-sig")
    assert len([l for l in csvt.split("\r\n") if l]) == 1  # yalnız başlık


# ── 4. Tek snapshot zinciri ─────────────────────────────────────────

def test_single_snapshot_chain(env):
    c, hist = env
    tl.append_snapshot(_snap("08"), hist)
    tljs = c.get("/api/workspace/timeline").get_json()
    assert tljs["total"] == 1 and tljs["entries"][0]["id"] == 1
    snap = c.get("/api/workspace/snapshot/1").get_json()["snapshot"]
    assert snap["portfolio_summary"]["total_value"] == "1234.56789012"
    recs = c.get("/api/workspace/recommendations").get_json()["items"]
    assert recs[0]["code"] == "NO_ACTION_NEEDED"
    assert recs[0]["occurrences"] == 1
    assert recs[0]["confidence_changed"] is False
    risk = c.get("/api/workspace/risk-evolution").get_json()["series"]
    assert len(risk) == 1 and risk[0]["risk_score"] == 71
    assert c.get("/api/workspace/search?status=OK").get_json()["total"] == 1
    # Kendisiyle karşılaştırma: özdeş
    cmpr = c.get("/api/workspace/compare?a=1&b=1").get_json()
    assert cmpr["identical"] is True and cmpr["differences"] == []
    # Export her iki formatta
    assert c.get("/api/workspace/export/snapshot/1?format=json"
                 ).status_code == 200
    csvt = c.get("/api/workspace/export/snapshot/1?format=csv"
                 ).data.decode("utf-8-sig")
    assert "1234.56789012" in csvt


# ── 5. Çoklu snapshot zinciri ───────────────────────────────────────

def test_multi_snapshot_chain(env):
    c, hist = env
    tl.append_snapshot(_snap("08"), hist)
    tl.append_snapshot(_snap(
        "09", status="PARTIAL", partial=True,
        risk_summary={"score": 55, "status": "IZLEME", "components": []},
        recommendations=[{"code": "NO_ACTION_NEEDED", "priority": 99,
                          "confidence": "LOW"}]), hist)
    tl.append_snapshot(_snap(
        "10", risk_summary={"score": 88, "status": "SAGLIKLI",
                            "components": []},
        recommendations=[{"code": "DATA_REVIEW", "priority": 3,
                          "confidence": "HIGH"}]), hist)
    # Deterministik sıralama (ekleme sırası, 1-tabanlı)
    ids = [e["id"] for e in c.get("/api/workspace/timeline"
                                  ).get_json()["entries"]]
    assert ids == [1, 2, 3]
    # Compare gerçek farkları bulur
    d = c.get("/api/workspace/compare?a=1&b=2").get_json()
    assert d["identical"] is False
    fields = {x["field"] for x in d["differences"]}
    assert any(f.startswith("status") for f in fields)
    # Recommendation değişimleri
    items = {i["code"]: i for i in
             c.get("/api/workspace/recommendations").get_json()["items"]}
    assert items["NO_ACTION_NEEDED"]["occurrences"] == 2
    assert items["NO_ACTION_NEEDED"]["confidence_changed"] is True
    assert items["DATA_REVIEW"]["occurrences"] == 1
    # Risk evrimi: geçmişteki gerçek skorlar, tahminsiz
    risk = c.get("/api/workspace/risk-evolution").get_json()
    assert [p["risk_score"] for p in risk["series"]] == [71, 55, 88]
    assert risk["forecast"] is None
    # Search kombinasyonları
    assert c.get("/api/workspace/search?status=PARTIAL&partial=true"
                 ).get_json()["total"] == 1
    assert c.get("/api/workspace/search?confidence=HIGH"
                 ).get_json()["total"] == 3
    assert c.get("/api/workspace/search?recommendation=DATA_REVIEW"
                 ).get_json()["entries"][0]["id"] == 3
    assert c.get(
        "/api/workspace/search?start=2026-07-26T09:00:00%2B00:00"
        "&end=2026-07-26T09:30:00%2B00:00").get_json()["total"] == 1
    # JSON/CSV export satır sayıları
    csvt = c.get("/api/workspace/export/timeline?format=csv"
                 ).data.decode("utf-8-sig")
    assert len([l for l in csvt.split("\r\n") if l]) == 4
    jexp = json.loads(c.get("/api/workspace/export/timeline?format=json"
                            ).data.decode())
    assert jexp["total"] == 3


# ── 6. Eski / kısmi şema kayıtları ──────────────────────────────────

def test_legacy_partial_schema_no_fabrication(env):
    c, hist = env
    tl.append_snapshot({"generated_at": "2026-07-26T07:00:00+00:00",
                        "status": None, "advisory_only": True}, hist)
    e = c.get("/api/workspace/timeline").get_json()["entries"][0]
    assert e["status"] is None and e["partial"] is None
    assert e["insight_count"] is None          # 0 DEĞİL
    assert e["recommendation_count"] is None
    p = c.get("/api/workspace/risk-evolution").get_json()["series"][0]
    assert p["risk_score"] is None and p["risk_status"] is None
    # CSV'de bilinmeyen → "—"; 0 türetilmez
    row = [l for l in c.get(
        "/api/workspace/export/risk-evolution?format=csv"
    ).data.decode("utf-8-sig").split("\r\n") if l][1]
    cells = row.split(",")
    assert cells[2] == "—" and "0" not in cells[2]


# ── 7-8. Decimal bütünlüğü + determinizm (tüm katmanlar) ────────────

def test_decimal_integrity_all_layers(env):
    c, hist = env
    tl.append_snapshot(_snap("08", portfolio_summary={
        "total_value": Decimal("0.10000000000000000001"),
        "unrealized_pnl": Decimal("-42.5")}), hist)
    # Timeline dosyasında string olarak saklanır
    stored = hist.read_text(encoding="utf-8")
    assert '"0.10000000000000000001"' in stored
    # API katmanı: float hassasiyet kaybı YOK
    raw = c.get("/api/workspace/snapshot/1").data.decode()
    assert '"0.10000000000000000001"' in raw
    assert '"-42.5"' in raw
    # Export JSON + CSV
    jraw = c.get("/api/workspace/export/snapshot/1?format=json"
                 ).data.decode()
    assert '"0.10000000000000000001"' in jraw
    craw = c.get("/api/workspace/export/snapshot/1?format=csv"
                 ).data.decode("utf-8-sig")
    assert "0.10000000000000000001" in craw
    assert "-42.5" in craw            # negatif Decimal DEĞİŞMEZ


def test_full_chain_determinism(env):
    c, hist = env
    tl.append_snapshot(_snap("08"), hist)
    tl.append_snapshot(_snap("09", partial=True, status="PARTIAL"), hist)
    eps = ("/api/workspace/timeline", "/api/workspace/snapshot/2",
           "/api/workspace/compare?a=1&b=2",
           "/api/workspace/recommendations",
           "/api/workspace/risk-evolution",
           "/api/workspace/search?status=OK",
           "/api/workspace/export/timeline?format=json",
           "/api/workspace/export/timeline?format=csv",
           "/api/workspace/export/compare?a=1&b=2&format=csv")
    for ep in eps:
        assert c.get(ep).data == c.get(ep).data, ep


# ── 2/12. Zincir bütünlüğü: okuma geçmişi değiştirmez ───────────────

def test_end_to_end_reads_leave_history_immutable(env):
    c, hist = env
    tl.append_snapshot(_snap("08"), hist)
    before = hist.read_bytes()
    for ep in READ_EPS + ("/api/workspace/snapshot/1",
                          "/api/workspace/compare?a=1&b=1",
                          "/api/workspace/export/timeline?format=csv",
                          "/api/workspace/export/search?format=json"):
        assert c.get(ep).status_code == 200, ep
    c.get("/workspace")
    assert hist.read_bytes() == before


# ── 9. 1500.1 davranış korunumu (hızlı kontrol) ─────────────────────

def test_1500_1_surface_intact(env):
    c, _ = env
    flask_app._intel_service = None
    r = c.get("/intelligence")
    assert r.status_code == 200
    r = c.get("/api/intelligence/summary")
    assert r.status_code == 200
    body = r.get_json()
    assert ("enabled" in body) or (body.get("ok") in (True, False))
    # Ana sayfalar hâlâ render olur
    for page in ("/", "/risk", "/portfolio"):
        assert c.get(page).status_code == 200, page
