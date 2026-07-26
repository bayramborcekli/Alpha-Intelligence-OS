"""Mission 1500.2 / Agent 03 — Workspace Service Layer testleri."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

import intelligence_timeline as tl
import intelligence_workspace_service as ws


def _snap(hour="10", **over):
    base = {
        "generated_at": f"2026-07-26T{hour}:00:00+00:00",
        "status": "OK",
        "partial": False,
        "freshness": [{"source": "account", "status": "OK"}],
        "insights": [{"code": "PORTFOLIO_OK", "confidence": "HIGH"}],
        "recommendations": [{"code": "NO_ACTION_NEEDED", "priority": 99,
                             "confidence": "MEDIUM"}],
        "warnings": [],
        "portfolio_summary": {"total_value": Decimal("123.45")},
        "risk_summary": {"score": 87, "status": "SAGLIKLI",
                         "components": [{"factor": "concentration",
                                         "penalty": "5"}]},
        "risk_explanations": [],
        "advisory_only": True,
    }
    base.update(over)
    return base


@pytest.fixture()
def hist(tmp_path):
    p = tmp_path / "history.jsonl"
    tl.append_snapshot(_snap("08"), p)
    tl.append_snapshot(
        _snap("09", status="PARTIAL", partial=True,
              insights=[{"code": "DATA_GAP", "confidence":
                         "INSUFFICIENT_DATA"}],
              recommendations=[{"code": "DATA_REVIEW", "priority": 3,
                                "confidence": "LOW"}],
              risk_summary={"score": 70, "status": "IZLENMELI",
                            "components": []}), p)
    tl.append_snapshot(
        _snap("10", portfolio_summary={"total_value": Decimal("200.00")},
              recommendations=[{"code": "NO_ACTION_NEEDED", "priority": 99,
                                "confidence": "HIGH"}]), p)
    return p


# ── timeline / snapshot okuma ────────────────────────────────────────

def test_timeline_read(hist):
    out = ws.get_timeline(path=hist)
    assert out["ok"] and out["read_only"] and out["advisory_only"]
    assert out["total"] == 3
    assert [e["id"] for e in out["entries"]] == [1, 2, 3]
    assert out["entries"][1]["status"] == "PARTIAL"
    assert out["entries"][0]["insight_count"] == 1


def test_timeline_limit_offset(hist):
    out = ws.get_timeline(limit=1, offset=1, path=hist)
    assert out["total"] == 3 and len(out["entries"]) == 1
    assert out["entries"][0]["id"] == 2


def test_snapshot_read(hist):
    out = ws.get_snapshot(2, path=hist)
    assert out["ok"] and out["id"] == 2
    assert out["snapshot"]["status"] == "PARTIAL"


def test_snapshot_not_found(hist):
    for bad in (0, 4, -1, True):
        out = ws.get_snapshot(bad, path=hist)
        assert out["ok"] is False
        assert out["error"]["code"] == "SNAPSHOT_NOT_FOUND"


# ── compare ──────────────────────────────────────────────────────────

def test_compare_reports_changed_fields(hist):
    out = ws.compare_snapshots(1, 3, path=hist)
    assert out["ok"] and not out["identical"]
    fields = {d["field"]: d for d in out["differences"]}
    assert fields["portfolio_summary.total_value"]["change"] == "CHANGED"
    assert fields["portfolio_summary.total_value"]["a"] == "123.45"
    assert fields["portfolio_summary.total_value"]["b"] == "200.00"


def test_compare_new_and_removed_marked_veri_yok(tmp_path):
    p = tmp_path / "h.jsonl"
    tl.append_snapshot(_snap("08", risk_summary={"score": 80}), p)
    tl.append_snapshot(_snap("09", risk_summary={"status": "OK"}), p)
    out = ws.compare_snapshots(1, 2, path=p)
    fields = {d["field"]: d for d in out["differences"]}
    assert fields["risk_summary.score"]["change"] == "REMOVED"
    assert fields["risk_summary.score"]["b"] == "Veri Yok"
    assert fields["risk_summary.status"]["change"] == "NEW"
    assert fields["risk_summary.status"]["a"] == "Veri Yok"


def test_compare_list_items_deep_diff(tmp_path):
    p = tmp_path / "h.jsonl"
    tl.append_snapshot(
        _snap("08", insights=[{"code": "A", "confidence": "HIGH"}]), p)
    tl.append_snapshot(
        _snap("09", insights=[{"code": "A", "confidence": "LOW"},
                              {"code": "B", "confidence": "HIGH"}]), p)
    out = ws.compare_snapshots(1, 2, path=p)
    fields = {d["field"]: d for d in out["differences"]}
    assert fields["insights[0].confidence"]["change"] == "CHANGED"
    assert fields["insights[1]"]["change"] == "NEW"
    assert fields["insights[1]"]["a"] == "Veri Yok"
    back = ws.compare_snapshots(2, 1, path=p)
    bfields = {d["field"]: d for d in back["differences"]}
    assert bfields["insights[1]"]["change"] == "REMOVED"
    assert bfields["insights[1]"]["b"] == "Veri Yok"


def test_search_duplicate_records_time_filter(tmp_path):
    p = tmp_path / "h.jsonl"
    tl.append_snapshot(_snap("08"), p)
    tl.append_snapshot(_snap("08"), p)  # değer-eşit kopya
    out = ws.search(start="2026-07-26T08:00:00+00:00",
                    end="2026-07-26T08:00:00+00:00", path=p)
    assert out["total"] == 2
    assert [e["id"] for e in out["entries"]] == [1, 2]


def test_compare_identical(hist):
    out = ws.compare_snapshots(1, 1, path=hist)
    assert out["ok"] and out["identical"] and out["differences"] == []


def test_compare_missing_snapshot(hist):
    out = ws.compare_snapshots(1, 99, path=hist)
    assert out["ok"] is False
    assert out["error"]["code"] == "SNAPSHOT_NOT_FOUND"


def test_compare_only_recorded_fields(hist):
    out = ws.compare_snapshots(1, 2, path=hist)
    assert set(out["compared_fields"]) <= set(tl.ALLOWED_FIELDS)
    assert "generated_at" not in out["compared_fields"]


# ── recommendation history ───────────────────────────────────────────

def test_recommendation_history_groups_and_changes(hist):
    out = ws.get_recommendation_history(path=hist)
    assert out["ok"]
    by_code = {i["code"]: i for i in out["items"]}
    assert set(by_code) == {"NO_ACTION_NEEDED", "DATA_REVIEW"}
    na = by_code["NO_ACTION_NEEDED"]
    assert na["occurrences"] == 2
    assert na["confidence_changed"] is True  # MEDIUM → HIGH
    assert na["priority_changed"] is False
    assert by_code["DATA_REVIEW"]["occurrences"] == 1


def test_recommendation_history_merges_consecutive_repeats(tmp_path):
    p = tmp_path / "h.jsonl"
    for h in ("08", "09", "10"):
        tl.append_snapshot(_snap(h), p)
    out = ws.get_recommendation_history(path=p)
    na = out["items"][0]
    assert na["occurrences"] == 3
    assert len(na["history"]) == 1  # ardışık tekrarlar birleşti
    assert na["history"][0]["count"] == 3
    assert na["history"][0]["last_snapshot_id"] == 3


# ── risk evolution ───────────────────────────────────────────────────

def test_risk_evolution_series_only_from_history(hist):
    out = ws.get_risk_evolution(path=hist)
    assert out["ok"]
    scores = [pt["risk_score"] for pt in out["series"]]
    assert scores == [87, 70, 87]
    assert out["series"][0]["risk_status"] == "SAGLIKLI"
    assert out["series"][0]["freshness"][0]["source"] == "account"
    assert out["forecast"] is None  # tahmin YOK


def test_risk_evolution_unknowns_stay_null(tmp_path):
    p = tmp_path / "h.jsonl"
    tl.append_snapshot(_snap("08", risk_summary=None), p)
    out = ws.get_risk_evolution(path=p)
    pt = out["series"][0]
    assert pt["risk_score"] is None and pt["risk_status"] is None
    assert pt["risk_factors"] is None


# ── search ───────────────────────────────────────────────────────────

def test_search_filters(hist):
    assert ws.search(status="PARTIAL", path=hist)["total"] == 1
    assert ws.search(partial=False, path=hist)["total"] == 2
    assert ws.search(recommendation_code="DATA_REVIEW",
                     path=hist)["entries"][0]["id"] == 2
    assert ws.search(insight_code="PORTFOLIO_OK", path=hist)["total"] == 2
    assert ws.search(confidence="INSUFFICIENT_DATA",
                     path=hist)["total"] == 1
    assert ws.search(advisory_only=True, path=hist)["total"] == 3


def test_search_timerange(hist):
    out = ws.search(start="2026-07-26T09:00:00+00:00",
                    end="2026-07-26T10:00:00+00:00", path=hist)
    assert [e["id"] for e in out["entries"]] == [2, 3]


def test_search_combined_no_match(hist):
    out = ws.search(status="PARTIAL",
                    recommendation_code="NO_ACTION_NEEDED", path=hist)
    assert out["total"] == 0 and out["entries"] == []


# ── boş / kısmi geçmiş, determinizm, Decimal, unknown ────────────────

def test_empty_history(tmp_path):
    p = tmp_path / "empty.jsonl"
    assert ws.get_timeline(path=p)["total"] == 0
    assert ws.get_recommendation_history(path=p)["items"] == []
    assert ws.get_risk_evolution(path=p)["series"] == []
    assert ws.search(path=p)["total"] == 0
    assert ws.get_snapshot(1, path=p)["ok"] is False


def test_partial_history_records_handled(tmp_path):
    p = tmp_path / "h.jsonl"
    tl.append_snapshot({"generated_at": "2026-07-26T08:00:00+00:00"}, p)
    tline = ws.get_timeline(path=p)
    assert tline["entries"][0]["insight_count"] is None  # 0 uydurulmadı
    assert ws.get_recommendation_history(path=p)["items"] == []


def test_determinism(hist):
    for fn in (lambda: ws.get_timeline(path=hist),
               lambda: ws.compare_snapshots(1, 3, path=hist),
               lambda: ws.get_recommendation_history(path=hist),
               lambda: ws.get_risk_evolution(path=hist),
               lambda: ws.search(path=hist)):
        assert fn() == fn()


def test_decimal_preserved_as_string(hist):
    snap = ws.get_snapshot(1, path=hist)["snapshot"]
    assert snap["portfolio_summary"]["total_value"] == "123.45"
    assert isinstance(snap["portfolio_summary"]["total_value"], str)


def test_advisory_only_on_every_envelope(hist):
    for out in (ws.get_timeline(path=hist), ws.get_snapshot(1, path=hist),
                ws.compare_snapshots(1, 2, path=hist),
                ws.get_recommendation_history(path=hist),
                ws.get_risk_evolution(path=hist), ws.search(path=hist)):
        assert out["advisory_only"] is True
        assert out["read_only"] is True


# ── sterile error / secret sızıntısı ─────────────────────────────────

def test_sterile_error_no_exception_details(monkeypatch, hist):
    def boom(path=None, limit=None):
        raise RuntimeError("SECRET-abc TRACE /home/user/x.py line 5")
    monkeypatch.setattr(tl, "load_history", boom)
    for out in (ws.get_timeline(path=hist),
                ws.get_recommendation_history(path=hist),
                ws.get_risk_evolution(path=hist), ws.search(path=hist)):
        assert out["ok"] is False
        text = str(out)
        assert "SECRET-abc" not in text and "TRACE" not in text
        assert "x.py" not in text
        assert out["error"]["message"] == "İşlem tamamlanamadı"


def test_no_secret_like_content_in_outputs(hist):
    text = str(ws.get_timeline(path=hist)) + \
        str(ws.get_snapshot(1, path=hist)) + \
        str(ws.get_recommendation_history(path=hist))
    for bad in ("api_key", "secret", "token", "cookie", "csrf"):
        assert bad not in text.lower()


def test_module_read_only_no_writes_no_network():
    src = Path("intelligence_workspace_service.py").read_text()
    tree = ast.parse(src)
    banned_imports = {"requests", "urllib", "http", "socket", "flask",
                      "exchange_gateway", "ledger_api", "auth", "hmac"}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for n in names:
            assert n not in banned_imports, f"yasak import: {n}"
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else \
                getattr(fn, "id", "")
            assert name not in ("open", "write", "unlink", "remove",
                                "append_snapshot"), \
                f"yazma/dosya çağrısı yasak: {name}"
