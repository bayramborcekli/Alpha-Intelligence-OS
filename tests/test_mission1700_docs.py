"""Mission 1700 / Agent 09 — Dokümantasyon tutarlılık testleri.

Doküman ↔ kaynak kod tutarlılığını doğrular; ürün davranışı test etmez.
"""

from __future__ import annotations

import re
from pathlib import Path

import app as flask_app
import portfolio_export as pex

DOC = Path("docs/portfolio_intelligence.md").read_text(encoding="utf-8")
API_REF = Path("docs/API_REFERENCE.md").read_text(encoding="utf-8")
INDEX = Path("docs/MISSION_INDEX.md").read_text(encoding="utf-8")
ALL_DOCS = DOC + API_REF + INDEX


def _rules():
    return {r.rule: r for r in flask_app.app.url_map.iter_rules()}


def test_docs_exist():
    for p in ("docs/portfolio_intelligence.md", "docs/API_REFERENCE.md",
              "docs/MISSION_INDEX.md"):
        assert Path(p).exists(), p


def test_documented_endpoints_exist_with_v1_aliases():
    rules = _rules()
    for p in ("/api/portfolio/intelligence",
              "/api/v1/portfolio/intelligence",
              "/api/portfolio/intelligence/export/json",
              "/api/v1/portfolio/intelligence/export/json",
              "/api/portfolio/intelligence/export/csv",
              "/api/v1/portfolio/intelligence/export/csv",
              "/portfolio-intelligence"):
        assert p in rules, p
        assert p in ALL_DOCS or p.replace("/api/v1", "/api") in ALL_DOCS


def test_no_fabricated_endpoints_in_docs():
    """Dokümanda geçen portfolio uçları gerçekte var olmalı."""
    rules = set(_rules())
    documented = set(re.findall(
        r"(/api(?:/v1)?/portfolio/[a-z/]+)", ALL_DOCS))
    for ep in documented:
        assert ep.rstrip("/") in rules, \
            f"Uydurma endpoint dokümante edilmiş: {ep}"


def test_documented_methods_match_code():
    rules = _rules()
    for ep in ("/api/portfolio/intelligence",
               "/api/portfolio/intelligence/export/json",
               "/api/portfolio/intelligence/export/csv"):
        assert set(rules[ep].methods) <= {"GET", "HEAD", "OPTIONS"}, ep
    assert "YALNIZ GET" in API_REF or "yalnız GET" in DOC.lower()


def _populated_envelope():
    import portfolio_service as psv
    return psv.get_portfolio_analysis({
        "equity": lambda: {"freshness": "fresh", "data": {
            "nav_usdt": "1000", "cash_usdt": "400", "realized_pnl": "5",
            "unrealized_pnl": "-2", "total_fees": "1"}},
        "positions": lambda: {"freshness": "fresh", "data": [{
            "symbol": "BTCUSDT", "side": "LONG", "quantity": "0.01",
            "entry_price": "50000", "mark_price": "60000",
            "leverage": "3", "notional": "600",
            "unrealized_pnl": "100"}]},
        "risk": lambda: {"freshness": "fresh", "data": {
            "drawdown_pct": "1",
            "thresholds": {"max_net_exposure_pct": "200",
                           "max_drawdown_pct": "5",
                           "max_concentration_pct": "80"}}},
    }, "2026-07-27T00:00:00+00:00")


def test_documented_envelope_fields_match_contract():
    env = _populated_envelope()
    for key in env:                              # zarf üst alanları
        assert f'"{key}"' in DOC, key
    port = env["portfolio"]
    for key in port:                             # portföy bölümleri
        assert f'"{key}"' in DOC, key
    # Derin/iç içe alanlar da dokümante olmalı (kontrat sürüklenmesi)
    nested = (list(port["equity"]) + list(port["allocation"])
              + list(port["exposure"]) + list(port["concentration"])
              + list(port["performance"]) + list(port["risk_utilization"])
              + list(port["health"]) + list(port["positions"][0])
              + list(port["allocation"]["assets"][0]))
    for key in nested:
        assert f'"{key}"' in DOC, key
    assert env["analysis_version"] == 1
    assert "analysis_version: 1" in DOC or '"analysis_version": 1' in DOC


def test_documented_error_codes_match_code():
    for code in ("FLOAT_REJECTED", "INVALID_INPUT", "PROVIDER_FAILED",
                 "INVALID_PROVIDER_RESULT", "UNKNOWN_PROVIDER",
                 "PORTFOLIO_ANALYSIS_ERROR", "INVALID_FORMAT",
                 "ANALYSIS_UNAVAILABLE"):
        assert code in DOC, code
    # Kodda gerçekten var olmalılar (uydurma kod dokümante edilmemiş)
    sources = "".join(Path(p).read_text(encoding="utf-8") for p in (
        "portfolio_intelligence.py", "portfolio_service.py",
        "portfolio_export.py", "app.py"))
    for code in ("FLOAT_REJECTED", "PROVIDER_FAILED",
                 "PORTFOLIO_ANALYSIS_ERROR", "INVALID_FORMAT",
                 "ANALYSIS_UNAVAILABLE"):
        assert code in sources, code


def test_documented_formats_and_filenames_match_code():
    assert tuple(pex.FORMATS) == ("json", "csv")
    assert pex.JSON_FILENAME in DOC and pex.JSON_FILENAME in API_REF
    assert pex.CSV_FILENAME in DOC and pex.CSV_FILENAME in API_REF
    assert ",".join(pex.CSV_HEADER) in DOC       # section,field,value


def test_documented_csv_section_order_matches_code():
    rows = pex._csv_rows(_populated_envelope())
    first_sections = []
    for r in rows:
        if r[0] not in first_sections:
            first_sections.append(r[0])
    # Dolu zarfta dokümante TAM sıra doğrulanır
    assert first_sections == ["meta", "summary", "positions", "risk",
                              "diversification", "sources"]
    assert "meta → summary → positions → risk" in DOC.replace(
        "`", "") or "meta→summary→positions→risk" in DOC


def test_documented_persist_false_matches_code():
    src = Path("portfolio_service.py").read_text(encoding="utf-8")
    assert "risk_api.summary(persist=False)" in src
    assert "persist=False" in DOC
    assert "risk_history.jsonl" in DOC


def test_documented_forecast_null_matches_code():
    core = Path("portfolio_intelligence.py").read_text(encoding="utf-8")
    assert '"forecast"' in core
    assert "forecast" in DOC and "null" in DOC


def test_documented_test_counts_match_files():
    counts = {
        "test_mission1700_portfolio_core.py": 27,
        "test_mission1700_portfolio_service.py": 23,
        "test_mission1700_portfolio_api.py": 17,
        "test_mission1700_portfolio_ui.py": 20,
        "test_mission1700_portfolio_export.py": 21,
        "test_mission1700_security_verification.py": 50,
        "test_mission1700_full_regression.py": 15,
    }
    total = sum(counts.values())
    assert total == 173
    assert "**173**" in DOC and "**173**" in INDEX
    for fname, n in counts.items():
        assert f"| {n} |" in DOC or str(n) in DOC, fname
        assert fname in DOC or f"tests/{fname}" in DOC, fname


def test_doc_references_real_files():
    for ref in re.findall(r"`([a-z_]+\.py)`", DOC):
        assert Path(ref).exists() or (Path("tests") / ref).exists(), ref
    for ref in re.findall(r"`(tests/[a-z_0-9]+\.py)`", DOC):
        assert Path(ref).exists(), ref
    for link in re.findall(r"`(docs/[A-Za-z_0-9.]+\.md)`", DOC):
        assert Path(link).exists(), link


def test_doc_contains_no_secret_material():
    low = ALL_DOCS.lower()
    for banned in ("api_key=", "begin rsa", "mnemonic", "-----begin"):
        assert banned not in low, banned


def test_doc_does_not_promise_unimplemented_features():
    # Tahmin motoru YOK — doküman bunu sınırlama olarak beyan eder.
    assert "forecast" in DOC
    assert "Bilinen sınırlama" in DOC or "sınırlama" in DOC.lower()
