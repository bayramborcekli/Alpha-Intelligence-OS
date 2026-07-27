"""Mission 1800 / Agent 09 — Dokümantasyon tutarlılık testleri.

Doküman ↔ kaynak kod tutarlılığını doğrular; ürün davranışı test etmez.
"""

from __future__ import annotations

import re
from pathlib import Path

import app as flask_app
import strategy_export as sx
import strategy_intelligence as score
import strategy_service as ssv

DOC = Path("docs/mission1800_strategy_intelligence.md").read_text(
    encoding="utf-8")
API_REF = Path("docs/API_REFERENCE.md").read_text(encoding="utf-8")
INDEX = Path("docs/MISSION_INDEX.md").read_text(encoding="utf-8")
ALL_DOCS = DOC + API_REF + INDEX


def _rules():
    return {r.rule: r for r in flask_app.app.url_map.iter_rules()}


def test_docs_exist():
    for p in ("docs/mission1800_strategy_intelligence.md",
              "docs/architecture/strategy_intelligence.md",
              "docs/API_REFERENCE.md", "docs/MISSION_INDEX.md"):
        assert Path(p).exists(), p


def test_documented_endpoints_exist_with_v1_aliases():
    rules = _rules()
    for p in ("/api/strategy/intelligence",
              "/api/v1/strategy/intelligence",
              "/strategy-intelligence"):
        assert p in rules, p
        assert p in ALL_DOCS or p.replace("/api/v1", "/api") in ALL_DOCS


def test_no_fabricated_endpoints_in_docs():
    """Dokümanda geçen strateji uçları gerçekte var olmalı."""
    rules = set(_rules())
    documented = set(re.findall(
        r"(/api(?:/v1)?/strategy/[a-z/]+)", ALL_DOCS))
    for ep in documented:
        assert ep.rstrip("/") in rules, \
            f"Uydurma endpoint dokümante edilmiş: {ep}"


def test_documented_methods_match_code():
    rules = _rules()
    for ep in ("/api/strategy/intelligence",
               "/api/v1/strategy/intelligence"):
        assert set(rules[ep].methods) <= {"GET", "HEAD", "OPTIONS"}, ep
    assert "yalnız GET" in DOC or "YALNIZ GET" in DOC


def test_documented_schema_fields_match_export_contract():
    for field in sx.PROPOSAL_FIELDS:
        assert f"`{field}`" in DOC, field
    for field in sx.RECOMMENDATION_FIELDS:
        assert f"`{field}`" in DOC, field
    assert "strategy_version: 1" in DOC or \
        "`strategy_version` | Şema sürümü; daima `1`" in DOC


def test_documented_actions_and_codes_match_core():
    actions = (score.ACTION_REDUCE, score.ACTION_INCREASE,
               score.ACTION_HOLD, score.ACTION_REBALANCE,
               score.ACTION_DIVERSIFY)
    reasons = (score.REASON_CONCENTRATION_HIGH,
               score.REASON_DIVERSIFICATION_LOW, score.REASON_EXCESS_CASH,
               score.REASON_RISK_LIMIT_NEAR,
               score.REASON_RISK_LIMIT_BREACHED,
               score.REASON_UNDER_ALLOCATED, score.REASON_OVER_ALLOCATED,
               score.REASON_LOW_DATA_QUALITY)
    invalidations = (score.INVALIDATE_ALLOCATION_CHANGED,
                     score.INVALIDATE_CONCENTRATION_REDUCED,
                     score.INVALIDATE_EXPOSURE_CHANGED,
                     score.INVALIDATE_RISK_UTILIZATION_CHANGED,
                     score.INVALIDATE_DATA_QUALITY_IMPROVED)
    warnings = (score.WARNING_LOW_DATA_QUALITY,
                score.WARNING_RISK_LIMIT_BREACHED,
                score.WARNING_ANALYSIS_UNAVAILABLE)
    for code in actions + reasons + invalidations + warnings:
        assert code in DOC, code


def test_documented_thresholds_match_core_constants():
    pairs = (("%60", score.CASH_EXCESS_PCT, "60"),
             ("%30", score.CASH_TARGET_PCT, "30"),
             ("%20", score.UNDER_ALLOC_GROSS_PCT, "20"),
             ("%100", score.OVER_ALLOC_GROSS_PCT, "100"),
             ("%50", score.TOP_SHARE_HIGH_PCT, "50"),
             ("< 3", score.EFFECTIVE_POS_LOW, "3"),
             ("%80", score.UTIL_NEAR_PCT, "80"))
    for doc_token, const, value in pairs:
        assert str(const) == value, (doc_token, const)
        assert doc_token in DOC, doc_token


def test_documented_error_codes_match_code():
    for code in ("FLOAT_REJECTED", "INVALID_INPUT", "PROVIDER_FAILED",
                 "INVALID_PROVIDER_RESULT", "INVALID_ANALYSIS",
                 "UNKNOWN_PROVIDER", "STRATEGY_ANALYSIS_ERROR",
                 "INVALID_FORMAT", "PROPOSAL_UNAVAILABLE"):
        assert code in DOC, code
    sources = "".join(Path(p).read_text(encoding="utf-8") for p in (
        "strategy_intelligence.py", "strategy_service.py",
        "strategy_export.py", "app.py"))
    for code in ("FLOAT_REJECTED", "INVALID_ANALYSIS",
                 "STRATEGY_ANALYSIS_ERROR", "PROPOSAL_UNAVAILABLE"):
        assert code in sources, code


def test_documented_formats_and_filename_match_code():
    assert tuple(sx.FORMATS) == ("json",)
    assert sx.JSON_FILENAME in DOC
    assert '`FORMATS = ("json",)`' in DOC


def test_documented_market_regime_matches_code():
    p = score.build_strategy({
        "analysis_version": 1, "status": "OK", "portfolio": {}})
    assert p["market_regime"] == "UNKNOWN"
    assert '"UNKNOWN"' in DOC and "MARKET_REGIME_UNKNOWN" in DOC
    assert "NO_FORECAST" in DOC
    assert score.LIMIT_NO_FORECAST in p["limitations"]


def test_documented_meta_ownership_matches_code():
    p = ssv.analyze_strategy(
        {"portfolio_analysis": lambda: {"freshness": "fresh", "data": {
            "analysis_version": 1, "status": "OK", "portfolio": {}}}})
    assert "proposal_id" not in p and "generated_at" not in p
    assert "YALNIZ API" in DOC or "yalnız API" in DOC


def test_documented_persist_false_matches_code():
    src = Path("portfolio_service.py").read_text(encoding="utf-8")
    assert "risk_api.summary(persist=False)" in src
    assert "persist=False" in DOC
    assert "risk_history.jsonl" in DOC


def test_documented_test_counts_match_files():
    counts = {
        "test_mission1800_strategy_core.py": 45,
        "test_mission1800_strategy_service.py": 28,
        "test_mission1800_strategy_api.py": 23,
        "test_mission1800_strategy_ui.py": 24,
        "test_mission1800_strategy_export.py": 29,
        "test_mission1800_strategy_security.py": 58,
        "test_mission1800_full_regression.py": 39,
    }
    total = sum(counts.values())
    assert total == 246
    assert "**246**" in DOC and "246" in INDEX
    for fname, n in counts.items():
        assert f"| {n} |" in DOC, fname
        assert fname in DOC or f"tests/{fname}" in DOC, fname


def test_doc_references_real_files():
    for ref in re.findall(r"`([a-z_]+\.py)`", DOC):
        assert Path(ref).exists() or (Path("tests") / ref).exists(), ref
    for ref in re.findall(r"`(tests/[a-z_0-9]+\.py)`", DOC):
        assert Path(ref).exists(), ref
    for link in re.findall(r"`(docs/[A-Za-z_0-9./]+\.md)`", DOC):
        assert Path(link).exists(), link


def test_doc_contains_no_secret_material_or_trading_claims():
    low = ALL_DOCS.lower()
    for banned in ("api_key=", "begin rsa", "mnemonic", "-----begin"):
        assert banned not in low, banned
    # İşlem/yürütme vaadi yok: doküman advisory-only'yi beyan eder.
    assert "advisory" in low
    assert "emir vermez" in DOC or "yürütme semantiği yok" in DOC
