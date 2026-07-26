"""Mission 1600 / Agent 09 — Dokümantasyon tutarlılık testleri.

Doküman ↔ kaynak kod tutarlılığını doğrular; ürün davranışı test etmez.
"""

from __future__ import annotations

import re
from pathlib import Path

import app as flask_app
import automation_engine
import automation_export_api as aex

DOC = Path("docs/automation.md").read_text(encoding="utf-8")
API_REF = Path("docs/API_REFERENCE.md").read_text(encoding="utf-8")
INDEX = Path("docs/MISSION_INDEX.md").read_text(encoding="utf-8")
ALL_DOCS = DOC + API_REF + INDEX

STATUS_FIELDS = ("enabled", "interval_minutes", "state", "running",
                 "run_id", "last_run_started_at", "last_run_finished_at",
                 "last_run_status", "last_error_code",
                 "last_snapshot_recorded", "next_due")


def _rules():
    return {r.rule: r for r in flask_app.app.url_map.iter_rules()}


def test_docs_exist():
    for p in ("docs/automation.md", "docs/API_REFERENCE.md",
              "docs/MISSION_INDEX.md"):
        assert Path(p).exists(), p


def test_documented_endpoints_exist_with_v1_aliases():
    rules = _rules()
    for p in ("/api/automation/status", "/api/v1/automation/status",
              "/api/automation/run", "/api/v1/automation/run",
              "/api/automation/export/status",
              "/api/v1/automation/export/status",
              "/automation"):
        assert p in rules, p
        assert p in ALL_DOCS or p.replace("/api/v1", "/api") in ALL_DOCS


def test_no_fabricated_endpoints_in_docs():
    """Dokümanda geçen automation uçları gerçekte var olmalı."""
    rules = set(_rules())
    documented = set(re.findall(r"`(?:GET|POST)?\s*(/api[^\s`\\|]*automation[^\s`\\|]*)", ALL_DOCS))
    documented |= set(re.findall(r"(/api(?:/v1)?/automation/[a-z/]+)", ALL_DOCS))
    for ep in documented:
        ep = ep.rstrip("/")
        if "*" in ep:  # glob/başlık gösterimi, endpoint değil
            continue
        assert ep in rules, f"Uydurma endpoint dokümante edilmiş: {ep}"


def test_absent_endpoints_documented_as_absent():
    rules = set(_rules())
    for absent in ("/api/automation/enable", "/api/automation/disable",
                   "/api/automation/export/history"):
        assert absent not in rules
    assert "history export" in DOC.lower() or "history" in DOC
    assert "enable" in DOC.lower()


def test_documented_status_fields_match_contract():
    for f in STATUS_FIELDS:
        assert f in DOC, f
        assert f in API_REF, f
    assert tuple(aex.STATUS_FIELDS) == STATUS_FIELDS


def test_documented_state_schema_matches_code():
    for f in automation_engine._EMPTY_STATE:
        assert f"`{f}`" in DOC, f


def test_documented_error_codes_match_code():
    for code in ("EXECUTION_FAILED", "TIMEOUT", "INVALID_RESULT",
                 "APPEND_FAILED", "INTERRUPTED", "DUPLICATE_RUN",
                 "AUTOMATION_DISABLED", "AUTOMATION_ERROR",
                 "INVALID_FORMAT", "STATUS_UNAVAILABLE"):
        assert code in DOC, code


def test_documented_env_vars_match_code():
    src = Path("automation_engine.py").read_text(encoding="utf-8")
    for var in ("ALPHA_AUTOMATION_ENABLED",
                "ALPHA_AUTOMATION_INTERVAL_MINUTES",
                "ALPHA_AUTOMATION_TIMEOUT_SECONDS",
                "ALPHA_AUTOMATION_STATE_PATH"):
        assert var in DOC, var
        assert var in src, var


def test_documented_defaults_match_code():
    assert automation_engine.DEFAULT_INTERVAL_MINUTES == 60
    assert automation_engine.DEFAULT_TIMEOUT_SECONDS == 120
    assert automation_engine.MIN_INTERVAL_MINUTES == 5
    assert automation_engine.MIN_TIMEOUT_SECONDS == 10
    for lit in ("60", "120", "5", "10"):
        assert lit in DOC


def test_documented_formats_and_filenames_match_code():
    assert tuple(aex.FORMATS) == ("json", "csv")
    assert "automation_status.json" in DOC
    assert "automation_status.csv" in DOC


def test_documented_methods_match_code():
    rules = _rules()
    assert "POST" in rules["/api/automation/run"].methods
    for ep in ("/api/automation/status", "/api/automation/export/status"):
        assert set(rules[ep].methods) <= {"GET", "HEAD", "OPTIONS"}, ep


def test_doc_references_real_files():
    for ref in re.findall(r"`([a-z_]+\.py)`", DOC):
        assert Path(ref).exists() or (Path("tests") / ref).exists(), ref
    for ref in re.findall(r"`(tests/[a-z_0-9*]+\.py)`", DOC):
        if "*" in ref:
            assert list(Path("tests").glob(Path(ref).name)), ref
        else:
            assert Path(ref).exists(), ref


def test_doc_internal_links_resolve():
    for link in re.findall(r"`(docs/[A-Za-z_0-9.]+\.md)`", ALL_DOCS):
        assert Path(link).exists(), link


def test_doc_contains_no_secret_material():
    low = ALL_DOCS.lower()
    for banned in ("api_key=", "begin rsa", "mnemonic", "-----begin"):
        assert banned not in low, banned
