"""Mission 1500.2 / Agent 02 — Intelligence Timeline Engine testleri."""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest

import intelligence_timeline as tl


def _snap(**over):
    base = {
        "generated_at": "2026-07-26T10:00:00+00:00",
        "status": "OK",
        "partial": False,
        "freshness": [{"source": "account", "status": "OK",
                       "age_seconds": 5}],
        "insights": [{"code": "PORTFOLIO_OK", "confidence": "HIGH"}],
        "recommendations": [{"code": "NO_ACTION_NEEDED", "priority": 99}],
        "warnings": [],
        "portfolio_summary": {"total_value": Decimal("123.45")},
        "risk_summary": {"score": 87},
        "risk_explanations": [],
        "advisory_only": True,
    }
    base.update(over)
    return base


@pytest.fixture()
def hist(tmp_path):
    return tmp_path / "history.jsonl"


# ── append-only davranışı ─────────────────────────────────────────────

def test_append_creates_and_appends(hist):
    tl.append_snapshot(_snap(), hist)
    tl.append_snapshot(_snap(generated_at="2026-07-26T11:00:00+00:00"), hist)
    assert tl.count(hist) == 2


def test_no_overwrite_existing_lines(hist):
    tl.append_snapshot(_snap(), hist)
    before = hist.read_text(encoding="utf-8")
    tl.append_snapshot(_snap(generated_at="2026-07-26T11:00:00+00:00"), hist)
    after = hist.read_text(encoding="utf-8")
    assert after.startswith(before)  # eski içerik bayt bayt korunur


def test_no_update_or_delete_functions_exist():
    public = [n for n in dir(tl) if not n.startswith("_")]
    banned = ("update", "delete", "remove", "truncate", "overwrite",
              "rewrite", "purge", "clear")
    for name in public:
        assert not any(b in name.lower() for b in banned)


def test_module_never_opens_for_write_or_truncate():
    tree = ast.parse(Path("intelligence_timeline.py").read_text())
    modes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "open":
                mode = "r"  # varsayılan mod
                if node.args and isinstance(node.args[0], ast.Constant):
                    mode = node.args[0].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value,
                                                       ast.Constant):
                        mode = kw.value.value
                modes.append(mode)
    assert modes, "open() çağrıları bulunmalı"
    for m in modes:
        assert m in ("a", "r"), f"yasak açma modu: {m}"


def test_history_full_stops_new_records_without_deleting(hist, monkeypatch):
    monkeypatch.setattr(tl, "MAX_RECORDS", 2)
    tl.append_snapshot(_snap(), hist)
    tl.append_snapshot(_snap(), hist)
    with pytest.raises(tl.TimelineError) as e:
        tl.append_snapshot(_snap(), hist)
    assert e.value.code == "HISTORY_FULL"
    assert tl.count(hist) == 2  # mevcut kayıtlar silinmedi


# ── sıra, son kayıt, tarih aralığı, boş geçmiş ───────────────────────

def test_order_preserved(hist):
    for h in ("08", "09", "10"):
        tl.append_snapshot(
            _snap(generated_at=f"2026-07-26T{h}:00:00+00:00"), hist)
    got = [r["generated_at"] for r in tl.load_history(hist)]
    assert got == [f"2026-07-26T{h}:00:00+00:00" for h in ("08", "09", "10")]


def test_get_latest(hist):
    for h in ("08", "09", "10"):
        tl.append_snapshot(
            _snap(generated_at=f"2026-07-26T{h}:00:00+00:00"), hist)
    last = tl.get_latest(1, hist)
    assert len(last) == 1
    assert last[0]["generated_at"] == "2026-07-26T10:00:00+00:00"
    last2 = tl.get_latest(2, hist)
    assert [r["generated_at"][11:13] for r in last2] == ["09", "10"]


def test_timerange_inclusive_bounds(hist):
    for h in ("08", "09", "10"):
        tl.append_snapshot(
            _snap(generated_at=f"2026-07-26T{h}:00:00+00:00"), hist)
    got = tl.get_by_timerange("2026-07-26T08:00:00+00:00",
                              "2026-07-26T09:00:00+00:00", hist)
    assert [r["generated_at"][11:13] for r in got] == ["08", "09"]
    assert tl.get_by_timerange(None, None, hist) and len(
        tl.get_by_timerange(None, None, hist)) == 3


def test_timerange_excludes_unparseable_timestamps(hist):
    tl.append_snapshot(_snap(generated_at=None), hist)
    assert tl.get_by_timerange("2026-01-01T00:00:00+00:00",
                               "2027-01-01T00:00:00+00:00", hist) == []


def test_empty_history(hist):
    assert tl.load_history(hist) == []
    assert tl.get_latest(3, hist) == []
    assert tl.count(hist) == 0
    assert tl.get_by_timerange(None, None, hist) == []


def test_corrupt_lines_skipped_not_repaired(hist):
    tl.append_snapshot(_snap(), hist)
    with hist.open("a", encoding="utf-8") as fh:
        fh.write("{bozuk json\n")
    raw_before = hist.read_text(encoding="utf-8")
    assert tl.count(hist) == 1
    assert hist.read_text(encoding="utf-8") == raw_before  # onarılmadı


# ── Decimal / determinizm / bilinmeyen değer ─────────────────────────

def test_decimal_stored_as_string(hist):
    tl.append_snapshot(_snap(), hist)
    rec = tl.get_latest(1, hist)[0]
    assert rec["portfolio_summary"]["total_value"] == "123.45"
    raw = hist.read_text(encoding="utf-8")
    assert '"123.45"' in raw


def test_float_rejected(hist):
    with pytest.raises(tl.TimelineError) as e:
        tl.append_snapshot(
            _snap(portfolio_summary={"total_value": 123.45}), hist)
    assert e.value.code == "FLOAT_FORBIDDEN"
    assert tl.count(hist) == 0


def test_deterministic_serialization():
    a = tl.build_record(_snap())
    b = tl.build_record(dict(reversed(list(_snap().items()))))
    assert tl._canonical_json(a) == tl._canonical_json(b)


def test_unknown_values_preserved_never_zeroed(hist):
    tl.append_snapshot(
        _snap(portfolio_summary={"total_value": None, "pnl": "—"},
              status=None), hist)
    rec = tl.get_latest(1, hist)[0]
    assert rec["portfolio_summary"]["total_value"] is None
    assert rec["portfolio_summary"]["pnl"] == "—"
    assert rec["status"] is None
    assert 0 not in (rec["portfolio_summary"]["total_value"],)


def test_missing_whitelisted_field_becomes_null_not_zero(hist):
    snap = _snap()
    del snap["risk_summary"]
    tl.append_snapshot(snap, hist)
    rec = tl.get_latest(1, hist)[0]
    assert rec["risk_summary"] is None


# ── advisory_only / secret / exchange alanları ───────────────────────

def test_advisory_only_forced_true(hist):
    tl.append_snapshot(_snap(advisory_only=False), hist)
    rec = tl.get_latest(1, hist)[0]
    assert rec["advisory_only"] is True
    assert rec["read_only"] is True


def test_generic_exchange_and_user_keys_rejected(hist):
    for bad in ({"exchange": "binance"}, {"by_exchange": {"a": 1}},
                {"user": {"name": "x"}}, {"users": []},
                {"nested": {"user_profile": {}}}):
        with pytest.raises(tl.TimelineError) as e:
            tl.append_snapshot(_snap(risk_summary=bad), hist)
        assert e.value.code == "FORBIDDEN_FIELD"
    assert tl.count(hist) == 0


def test_cap_check_and_append_share_one_lock(monkeypatch, hist):
    """Tavan denetimi ile yazma aynı özel kilit altında yapılmalı."""
    import ast as _ast
    src = Path("intelligence_timeline.py").read_text()
    assert "fcntl.flock" in src and "LOCK_EX" in src
    tree = _ast.parse(src)
    fn = next(n for n in _ast.walk(tree)
              if isinstance(n, _ast.FunctionDef)
              and n.name == "append_snapshot")
    body_src = _ast.get_source_segment(src, fn)
    # Kilit, tavan denetiminden ÖNCE alınmalı.
    assert body_src.index("LOCK_EX") < body_src.index("MAX_RECORDS")


def test_secret_like_fields_rejected(hist):
    for bad in ({"api_key": "x"}, {"nested": {"session_token": "y"}},
                {"list": [{"cookie": "z"}]}, {"csrf_token": "q"}):
        with pytest.raises(tl.TimelineError) as e:
            tl.append_snapshot(_snap(risk_summary=bad), hist)
        assert e.value.code == "FORBIDDEN_FIELD"
    assert tl.count(hist) == 0


def test_non_whitelisted_topfields_dropped(hist):
    tl.append_snapshot(
        _snap(source_errors={"x": 1}, extra_field="drop-me"), hist)
    rec = tl.get_latest(1, hist)[0]
    assert "source_errors" not in rec
    assert "extra_field" not in rec
    assert set(rec) == {"v", "read_only"} | set(tl.ALLOWED_FIELDS)


def test_no_exchange_or_ledger_fields_stored(hist):
    with pytest.raises(tl.TimelineError):
        tl.append_snapshot(_snap(risk_summary={"exchange_response": {}}),
                           hist)
    with pytest.raises(tl.TimelineError):
        tl.append_snapshot(_snap(risk_summary={"ledger_rows": []}), hist)


def test_module_has_no_network_or_exchange_imports():
    tree = ast.parse(Path("intelligence_timeline.py").read_text())
    banned = {"requests", "urllib", "http", "socket", "exchange_gateway",
              "ledger_api", "auth", "flask", "hmac"}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for n in names:
            assert n not in banned, f"yasak import: {n}"


def test_record_too_large_rejected(hist):
    big = {"note": "x" * (tl.MAX_RECORD_BYTES + 100)}
    with pytest.raises(tl.TimelineError) as e:
        tl.append_snapshot(_snap(risk_summary=big), hist)
    assert e.value.code == "RECORD_TOO_LARGE"
    assert tl.count(hist) == 0
