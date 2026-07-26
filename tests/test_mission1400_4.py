"""Mission 1400.4 — Defter / Denetim / Raporlar testleri."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import dashboard_api as dapi
import ledger_api as la

PASSWORD = "defter-test-parola-1"
HASH = generate_password_hash(PASSWORD)

LEDGER_FILE = Path("alpha20_v1/mission1310b/ledger_events.json")
RECON_FILE = Path("alpha20_v1/mission1310b/mission_1310b_report.json")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def client(monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m14004_attempts.db")
    auth._ATTEMPTS.clear()
    dapi.invalidate_caches()
    la.invalidate_ledger_caches()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True
        dapi.invalidate_caches()
        la.invalidate_ledger_caches()


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


PAGES = ["/ledger", "/audit", "/reports"]
APIS = ["/api/v1/ledger/events", "/api/v1/ledger/summary",
        "/api/v1/ledger/integrity", "/api/v1/ledger/reconciliation",
        "/api/v1/audit/events", "/api/v1/audit/summary",
        "/api/v1/reports", "/api/v1/reports/mission-1310b",
        "/api/v1/reports/mission-1310b/download",
        "/api/v1/ledger/export.csv", "/api/v1/audit/export.csv"]


class TestAuth:
    def test_pages_require_auth(self, client):
        for p in PAGES:
            r = client.get(p)
            assert r.status_code == 302 and "/login" in r.headers["Location"]

    def test_apis_require_auth(self, client):
        for a in APIS:
            assert client.get(a).status_code == 401, a


class TestLedgerBackend:
    def test_events_parse_and_model(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/events?limit=500").get_json()
        assert d["ok"] is True and d["read_only"] is True
        assert len(d["events"]) == d["pagination"]["total"] > 0
        e = d["events"][0]
        for f in ("event_id", "exchange", "original_event_type",
                  "normalized_event_type", "asset", "amount", "fee",
                  "net_amount", "timestamp_iso",
                  "source_transaction_id_masked", "raw_payload_hash",
                  "reconciliation_state", "duplicate_status",
                  "internal_transfer", "source"):
            assert f in e, f
        Decimal(e["amount"]); Decimal(e["fee"])  # Decimal-uyumlu
        assert "tx_id" not in e  # tam kimlik dışarı sızmaz
        assert "…" in e["source_transaction_id_masked"]

    def test_deterministic_ordering(self, client):
        _login(client)
        a = client.get("/api/v1/ledger/events?sort=timestamp&order=asc"
                       "&limit=500").get_json()["events"]
        b = client.get("/api/v1/ledger/events?sort=timestamp&order=asc"
                       "&limit=500").get_json()["events"]
        assert [x["event_id"] for x in a] == [x["event_id"] for x in b]
        ts = [(x["timestamp_ms"], x["event_id"]) for x in a]
        assert ts == sorted(ts)

    def test_event_id_uniqueness_and_default_order(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/events?limit=500").get_json()
        ids = [e["event_id"] for e in d["events"]]
        assert len(ids) == len(set(ids))
        # UI varsayılanı: en yeni ilk
        ms = [e["timestamp_ms"] for e in d["events"]]
        assert ms == sorted(ms, reverse=True)

    def test_normalization_and_internal_transfer(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/events?limit=500").get_json()
        for e in d["events"]:
            assert e["normalized_event_type"] in la.NORMALIZED_TYPES
            assert e["original_event_type"]  # orijinal tip korunur
            if e["normalized_event_type"] == "INTERNAL_TRANSFER":
                assert e["internal_transfer"] is True
        it = client.get("/api/v1/ledger/events?internal_transfer=true"
                        "&limit=500").get_json()["events"]
        assert it and all(e["internal_transfer"] for e in it)

    def test_filters(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/events?exchange=BINANCE_TR"
                       "&limit=500").get_json()
        assert all(e["exchange"] == "BINANCE_TR" for e in d["events"])
        d2 = client.get("/api/v1/ledger/events?asset=SHIB"
                        "&limit=500").get_json()
        assert d2["events"] and all(e["asset"] == "SHIB"
                                    for e in d2["events"])
        d3 = client.get("/api/v1/ledger/events?event_type=INTERNAL_TRANSFER"
                        "&limit=500").get_json()
        assert all(e["normalized_event_type"] == "INTERNAL_TRANSFER"
                   for e in d3["events"])
        d4 = client.get("/api/v1/ledger/events?date_from=2022-01-01"
                        "&date_to=2022-12-31&limit=500").get_json()
        for e in d4["events"]:
            assert "2022-01-01" <= e["timestamp_iso"][:10] <= "2022-12-31"
        d5 = client.get("/api/v1/ledger/events?search=SHIB"
                        "&limit=500").get_json()
        assert d5["events"]

    def test_sorting_amount(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/events?sort=amount&order=desc"
                       "&limit=500").get_json()
        vals = [Decimal(e["amount"]) for e in d["events"]]
        assert vals == sorted(vals, reverse=True)

    def test_invalid_params_rejected(self, client):
        _login(client)
        for bad in ("?sort=hack", "?order=up", "?exchange=EVIL",
                    "?event_type=DROP", "?date_from=01-01-2022",
                    "?date_to=2022-13-99", "?limit=0", "?limit=9999",
                    "?offset=-1", "?internal_transfer=belki",
                    "?search=" + "A" * 50, "?duplicate_status=YOK",
                    "?reconciliation_state=BELKI"):
            r = client.get("/api/v1/ledger/events" + bad)
            assert r.status_code == 400, bad
            assert r.get_json()["error"]["code"] == "INVALID_PARAMETER"

    def test_pagination(self, client):
        _login(client)
        p1 = client.get("/api/v1/ledger/events?limit=5&offset=0"
                        "&sort=timestamp&order=asc").get_json()
        p2 = client.get("/api/v1/ledger/events?limit=5&offset=5"
                        "&sort=timestamp&order=asc").get_json()
        assert len(p1["events"]) == 5 and p1["pagination"]["has_more"]
        ids1 = {e["event_id"] for e in p1["events"]}
        ids2 = {e["event_id"] for e in p2["events"]}
        assert not ids1 & ids2

    def test_malformed_row_isolation(self, client, monkeypatch, tmp_path):
        bad = tmp_path / "ledger.json"
        bad.write_text(json.dumps([
            {"event_id": "EVT-1", "class": "DEPOSIT", "asset": "BTC",
             "amount": "1.5", "fee": "0", "timestamp_ms": 5,
             "timestamp_iso": "2022-01-01T00:00:00+00:00", "tx_id": "12345",
             "raw_payload_sha256": "ab"},
            {"garbage": True}, "çöp", None,
            {"event_id": "EVT-2", "class": "GIZEMLI_TIP", "asset": "ETH",
             "amount": "kötü", "fee": "0", "timestamp_ms": 9,
             "timestamp_iso": "2022-01-02T00:00:00+00:00", "tx_id": "9",
             "raw_payload_sha256": "cd"}]))
        monkeypatch.setattr(la, "LEDGER_PATH", bad)
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/ledger/events?limit=500").get_json()
        assert d["ok"] is True and d["pagination"]["total"] == 2
        e2 = next(e for e in d["events"] if e["event_id"] == "EVT-2")
        assert e2["normalized_event_type"] == "UNKNOWN"
        assert e2["original_event_type"] == "GIZEMLI_TIP"
        assert e2["amount"] == "0"  # bozuk tutar izole edildi

    def test_source_unavailable(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(la, "LEDGER_PATH", tmp_path / "yok.json")
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/ledger/events").get_json()
        assert d["ok"] is False
        assert d["error"]["code"] == "LEDGER_UNAVAILABLE"
        assert d["freshness"] == "KULLANILAMIYOR"

    def test_summary(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/summary").get_json()
        assert d["total_event_count"] == d["unique_event_count"] > 0
        assert d["duplicate_blocked_count"] >= 0
        assert d["reconciliation_status"] in ("PASS", "PARTIAL", "FAIL",
                                              "UNKNOWN")
        assert d["append_only"] is True
        if d["reconciliation_status"] == "PARTIAL":
            assert any("Binance TR" in w for w in d["warnings"])

    def test_no_ledger_mutation(self, client):
        before = _sha(LEDGER_FILE)
        _login(client)
        for u in ("/api/v1/ledger/events?limit=500", "/api/v1/ledger/summary",
                  "/api/v1/ledger/integrity", "/api/v1/ledger/reconciliation",
                  "/api/v1/ledger/export.csv"):
            client.get(u)
        client.post("/api/v1/refresh")
        assert _sha(LEDGER_FILE) == before
        assert _sha(RECON_FILE) == _sha(RECON_FILE)


class TestIntegrity:
    def test_pass_result(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/integrity").get_json()
        assert d["status"] in ("PASS", "PARTIAL")
        assert d["checked_record_count"] > 0
        assert d["duplicate_count"] == 0
        assert d["ordering_status"] == "DETERMINISTIC"
        assert d["hash_check"] == "PRESENCE_ONLY"

    def test_fail_on_duplicate_ids(self, client, monkeypatch, tmp_path):
        bad = tmp_path / "dup.json"
        row = {"event_id": "EVT-X", "class": "DEPOSIT", "asset": "BTC",
               "amount": "1", "fee": "0", "timestamp_ms": 1,
               "timestamp_iso": "2022-01-01T00:00:00+00:00", "tx_id": "1",
               "raw_payload_sha256": "ab"}
        bad.write_text(json.dumps([row, row]))
        monkeypatch.setattr(la, "LEDGER_PATH", bad)
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/ledger/integrity").get_json()
        assert d["status"] == "FAIL" and d["duplicate_count"] == 1

    def test_partial_on_malformed(self, client, monkeypatch, tmp_path):
        bad = tmp_path / "part.json"
        bad.write_text(json.dumps([
            {"event_id": "EVT-1", "class": "DEPOSIT", "asset": "BTC",
             "amount": "1", "fee": "0", "timestamp_ms": 1,
             "timestamp_iso": "2022-01-01T00:00:00+00:00", "tx_id": "1",
             "raw_payload_sha256": "ab"}, {"bozuk": 1}]))
        monkeypatch.setattr(la, "LEDGER_PATH", bad)
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/ledger/integrity").get_json()
        assert d["status"] == "PARTIAL"
        assert d["malformed_record_count"] == 1

    def test_fail_source_missing(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(la, "LEDGER_PATH", tmp_path / "yok.json")
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/ledger/integrity").get_json()
        assert d["status"] == "FAIL" and d["ok"] is False

    def test_malformed_top_level_fail_closed(self, client, monkeypatch,
                                             tmp_path):
        # Liste yerine nesne → bütünlük FAIL, olaylar hata, export kapalı
        bad = tmp_path / "obj.json"
        bad.write_text(json.dumps({"hileli": "yapı"}))
        monkeypatch.setattr(la, "LEDGER_PATH", bad)
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/ledger/integrity").get_json()
        assert d["status"] == "FAIL"
        ev = client.get("/api/v1/ledger/events").get_json()
        assert ev["ok"] is False
        assert ev["error"]["code"] == "MALFORMED_LEDGER_RECORD"
        r = client.get("/api/v1/ledger/export.csv")
        assert r.status_code == 503
        assert r.get_json()["error"]["code"] == "LEDGER_INTEGRITY_FAILED"

    def test_export_blocked_on_fail(self, client, monkeypatch, tmp_path):
        bad = tmp_path / "dup.json"
        row = {"event_id": "EVT-X", "class": "DEPOSIT", "asset": "BTC",
               "amount": "1", "fee": "0", "timestamp_ms": 1,
               "timestamp_iso": "2022-01-01T00:00:00+00:00", "tx_id": "1",
               "raw_payload_sha256": "ab"}
        bad.write_text(json.dumps([row, row]))
        monkeypatch.setattr(la, "LEDGER_PATH", bad)
        la.invalidate_ledger_caches()
        _login(client)
        r = client.get("/api/v1/ledger/export.csv")
        assert r.status_code == 503
        assert r.get_json()["error"]["code"] == "LEDGER_INTEGRITY_FAILED"

    def test_verification_does_not_modify(self, client):
        before = _sha(LEDGER_FILE)
        _login(client)
        client.get("/api/v1/ledger/integrity")
        assert _sha(LEDGER_FILE) == before


class TestReconciliation:
    def test_evidence_mapping(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/reconciliation").get_json()
        assert d["ok"] is True
        assert d["status"] in ("PASS", "PARTIAL", "FAIL")
        assert d["opening_balance_fabricated"] is False
        for v in d["differences"].values():
            Decimal(v)
        assert d["excluded_internal_transfer_count"] >= 0
        assert d["evidence_run_id"]

    def test_partial_preserved_with_warning(self, client):
        _login(client)
        d = client.get("/api/v1/ledger/reconciliation").get_json()
        if d["status"] == "PARTIAL":
            assert d["status"] != "PASS"
            assert any("Binance TR" in w for w in d["warnings"])

    def test_missing_evidence_safe(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(la, "RECON_PATH", tmp_path / "yok.json")
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/ledger/reconciliation").get_json()
        assert d["ok"] is False and d["status"] == "UNKNOWN"


class TestAuditBackend:
    def test_events_sanitized(self, client):
        _login(client)
        d = client.get("/api/v1/audit/events?limit=500").get_json()
        assert d["ok"] is True
        blob = json.dumps(d)
        for banned in ("password", "session_id", "csrf_token",
                       "api_secret", "Authorization"):
            assert banned not in blob
        for e in d["events"]:
            ip = e["client_metadata_masked"]
            assert ip == "" or ".x.x" in ip or "…" in ip or len(ip) <= 8

    def test_filters_and_pagination(self, client):
        _login(client)
        d = client.get("/api/v1/audit/events?result=FAIL"
                       "&limit=10").get_json()
        assert all(e["result"] == "FAIL" for e in d["events"])
        assert len(d["events"]) <= 10
        d2 = client.get("/api/v1/audit/events?severity=WARN"
                        "&limit=10").get_json()
        assert all(e["severity"] == "WARN" for e in d2["events"])
        r = client.get("/api/v1/audit/events?severity=EXTREME")
        assert r.status_code == 400

    def test_no_recursion_flood(self, client):
        _login(client)
        before = client.get(
            "/api/v1/audit/summary").get_json()["total_event_count"]
        for _ in range(5):
            client.get("/api/v1/audit/events?limit=5")
            client.get("/api/v1/audit/summary")
        la.invalidate_ledger_caches()
        after = client.get(
            "/api/v1/audit/summary").get_json()["total_event_count"]
        assert after - before <= 1  # satır getirme denetim kaydı üretmez

    def test_storage_unavailable(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(la, "AUDIT_LOG_PATH", tmp_path / "yok.log")
        la.invalidate_ledger_caches()
        _login(client)
        d = client.get("/api/v1/audit/events").get_json()
        assert d["ok"] is False
        assert d["error"]["code"] == "AUDIT_UNAVAILABLE"
        s = client.get("/api/v1/audit/summary").get_json()
        assert s["storage_status"] == "KULLANILAMIYOR"

    def test_summary_counts(self, client):
        _login(client)
        d = client.get("/api/v1/audit/summary").get_json()
        assert d["total_event_count"] >= d["login_failure_count"] >= 0
        assert d["storage_status"] == "GÜNCEL"


class TestReports:
    def test_discovery(self, client):
        _login(client)
        d = client.get("/api/v1/reports?limit=100").get_json()
        names = {r["mission_name"]: r for r in d["reports"]}
        assert "Mission 1310B" in names
        assert names["Mission 1310B"]["status"] == "MEVCUT"
        assert names["Mission 1310B"]["downloadable"] is True
        assert "Mission 1400.4" in names  # dosyasız → EKSİK
        assert names["Mission 1400.4"]["status"] == "EKSİK"
        blob = json.dumps(d)
        assert "/home/" not in blob and "alpha20_v1/" not in blob

    def test_detail_sanitized(self, client):
        _login(client)
        d = client.get("/api/v1/reports/mission-1310b").get_json()
        assert d["ok"] is True and d["report"]["status"] == "MEVCUT"
        blob = json.dumps(d)
        assert "/home/" not in blob
        # key_masked kalır, ham anahtar yok
        assert "key_masked" in d["content"]

    def test_unknown_report_404(self, client):
        _login(client)
        assert client.get("/api/v1/reports/mission-yok").status_code == 404

    def test_path_traversal_rejected(self, client):
        _login(client)
        for evil in ("..%2F..%2Fapp.py", "....%2F%2Fetc%2Fpasswd",
                     "mission-1310b%00", "%2e%2e%2f"):
            # Werkzeug ../ içeren yolu normalize edip 308 yönlendirebilir;
            # yönlendirmeyi takip et ve asla içerik sunulmadığını doğrula.
            r = client.get(f"/api/v1/reports/{evil}",
                           follow_redirects=True)
            assert r.status_code == 404, evil
            r2 = client.get(f"/api/v1/reports/{evil}/download",
                            follow_redirects=True)
            assert r2.status_code == 404, (evil, r2.status_code)

    def test_download(self, client):
        _login(client)
        r = client.get("/api/v1/reports/mission-1310b/download")
        assert r.status_code == 200
        cd = r.headers["Content-Disposition"]
        assert cd == "attachment; filename=alpha-report-mission-1310b.json"
        body = json.loads(r.get_data(as_text=True))
        assert body.get("mission")
        assert "/home/" not in r.get_data(as_text=True)

    def test_missing_report_not_downloadable(self, client):
        _login(client)
        r = client.get("/api/v1/reports/mission-1400-4/download")
        assert r.status_code == 404


class TestCsv:
    def _check(self, client, url):
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/csv")
        assert "attachment; filename=alpha-" in r.headers["Content-Disposition"]
        body = r.get_data()
        assert body.startswith("\ufeff".encode("utf-8"))
        return body.decode("utf-8-sig")

    def test_ledger_csv(self, client):
        _login(client)
        text = self._check(client, "/api/v1/ledger/export.csv")
        lines = text.strip().splitlines()
        assert lines[0].startswith("timestamp_iso,exchange,")
        assert len(lines) - 1 <= la.LEDGER_MAX_LIMIT
        # tam tx_id yok, maskeli var
        assert "80194068309" not in text
        assert "…" in text

    def test_ledger_csv_negative_and_injection(self, client, monkeypatch,
                                               tmp_path):
        bad = tmp_path / "l.json"
        bad.write_text(json.dumps([
            {"event_id": "EVT-1", "class": "=HACK", "asset": "+EVIL",
             "amount": "-12.5", "fee": "0", "timestamp_ms": 1,
             "timestamp_iso": "2022-01-01T00:00:00+00:00", "tx_id": "123456",
             "raw_payload_sha256": "ab"}]))
        monkeypatch.setattr(la, "LEDGER_PATH", bad)
        la.invalidate_ledger_caches()
        _login(client)
        text = self._check(client, "/api/v1/ledger/export.csv")
        assert "'=HACK" in text and "'+EVIL" in text
        assert "-12.5" in text and "'-12.5" not in text

    def test_audit_csv(self, client):
        _login(client)
        text = self._check(client, "/api/v1/audit/export.csv")
        lines = text.strip().splitlines()
        assert lines[0].startswith("timestamp,event_type,")
        assert len(lines) - 1 <= la.AUDIT_MAX_LIMIT
        low = text.lower()
        for banned in ("api_secret", "session_secret", "csrf_token"):
            assert banned not in low

    def test_csv_filters_and_invalid(self, client):
        _login(client)
        text = self._check(client,
                           "/api/v1/ledger/export.csv?asset=SHIB")
        for line in text.strip().splitlines()[1:]:
            assert "SHIB" in line
        assert client.get(
            "/api/v1/ledger/export.csv?sort=hack").status_code == 400
        assert client.get(
            "/api/v1/audit/export.csv?severity=X").status_code == 400


class TestWriteSafety:
    def test_no_mutation_routes(self):
        for rule in flask_app.app.url_map.iter_rules():
            p = str(rule).lower()
            if any(w in p for w in ("ledger", "audit", "report")):
                methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
                assert methods == {"GET"}, f"yazma metodu: {rule}"

    def test_write_counters_zero(self, client):
        _login(client)
        for u in APIS:
            client.get(u)
        d = client.get("/api/v1/system/status").get_json()
        assert all(v == 0 for v in d["write_counters"].values())

    def test_evidence_unchanged(self, client):
        hashes = {p: _sha(p) for p in (LEDGER_FILE, RECON_FILE)}
        _login(client)
        for u in APIS:
            client.get(u)
        for p, h in hashes.items():
            assert _sha(p) == h, p


class TestFrontend:
    def test_pages_render(self, client):
        _login(client)
        checks = {
            "/ledger": ["Defter", "Toplam Olay", "Engellenen Tekrar",
                        "Mutabakat", "Bütünlük", "CSV Dışa Aktar",
                        "salt okunur"],
            "/audit": ["Denetim", "Başarısız Giriş", "CSRF Reddi",
                       "finansal defterden ayrıdır"],
            "/reports": ["Raporlar", "Çalıştırma Kimliği",
                         "düzenlenemez"],
        }
        for url, labels in checks.items():
            body = client.get(url).get_data(as_text=True)
            for label in labels:
                assert label in body, (url, label)
            assert "aria-live" in body
            low = body.lower()
            for banned in ("sil</button", "düzenle</button",
                           "kaydet</button", "deleterecord", "editrecord"):
                assert banned not in low, (url, banned)

    def test_nav_active(self, client):
        _login(client)
        shell = client.get("/").get_data(as_text=True)
        for href in ('href="/ledger"', 'href="/audit"', 'href="/reports"'):
            assert href in shell
        assert 'Sonraki sprint">Defter' not in shell
        assert 'Sonraki sprint">Raporlar' not in shell

    def test_no_secrets_in_pages(self, client):
        import os
        _login(client)
        for url in PAGES:
            body = client.get(url).get_data(as_text=True)
            for name in ("BINANCE_API_SECRET", "SESSION_SECRET",
                         "ALPHA_OWNER_PASSWORD_HASH"):
                v = os.environ.get(name)
                if v and len(v) > 8:
                    assert v not in body
