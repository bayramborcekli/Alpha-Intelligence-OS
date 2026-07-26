"""Mission 1500.2 / Agent 09 — Dokümantasyon tutarlılık testleri.

Doküman ↔ kaynak kod tutarlılığını doğrular; ürün davranışı test etmez.
"""

from __future__ import annotations

import re
from pathlib import Path

import app as flask_app
import intelligence_timeline as tl

NOTES = Path("docs/RELEASE_NOTES_1500_2.md").read_text(encoding="utf-8")
API_REF = Path("docs/API_REFERENCE.md").read_text(encoding="utf-8")
CHANGELOG = Path("CHANGELOG.md").read_text(encoding="utf-8")
INDEX = Path("docs/MISSION_INDEX.md").read_text(encoding="utf-8")
TESTPROG = Path("docs/TEST_PROGRAM.md").read_text(encoding="utf-8")
ALL_DOCS = NOTES + API_REF + CHANGELOG + INDEX + TESTPROG


def _rules():
    return {r.rule: r for r in flask_app.app.url_map.iter_rules()}


def test_docs_exist():
    for p in ("docs/RELEASE_NOTES_1500_2.md", "docs/API_REFERENCE.md",
              "docs/MISSION_INDEX.md", "docs/TEST_PROGRAM.md",
              "CHANGELOG.md", "docs/architecture.md"):
        assert Path(p).exists(), p


def test_documented_endpoints_exist_with_v1_aliases():
    rules = _rules()
    surfaces = ("timeline", "compare", "recommendations",
                "risk-evolution", "search")
    for s in surfaces:
        for prefix in ("/api/workspace", "/api/v1/workspace",
                       "/api/workspace/export",
                       "/api/v1/workspace/export"):
            assert f"{prefix}/{s}" in rules, f"{prefix}/{s}"
    for p in ("/api/workspace/snapshot/<snapshot_id>",
              "/api/v1/workspace/snapshot/<snapshot_id>",
              "/api/workspace/export/snapshot/<snapshot_id>",
              "/api/v1/workspace/export/snapshot/<snapshot_id>",
              "/workspace"):
        assert p in rules, p


def test_documented_methods_match_code():
    for rule, r in _rules().items():
        if "/workspace" in rule:
            assert set(r.methods) <= {"GET", "HEAD", "OPTIONS"}, rule
    assert "yalnız GET" in NOTES or "GET-only" in NOTES + CHANGELOG


def test_documented_params_match_code():
    import inspect
    src = inspect.getsource(flask_app)
    for param in ("limit", "offset", "recommendation", "insight",
                  "advisory_only", "partial", "confidence", "status",
                  "date_end", "format"):
        assert f'"{param}"' in src, param
        assert param in NOTES, param


def test_documented_limits_match_code():
    assert tl.MAX_RECORDS == 5000
    assert tl.MAX_RECORD_BYTES == 16384
    assert str(tl.DEFAULT_HISTORY_PATH) == "intelligence_history.jsonl"
    assert "MAX_RECORDS = 5000" in NOTES
    assert "MAX_RECORD_BYTES = 16384" in NOTES
    assert "intelligence_history.jsonl" in NOTES
    assert "ALPHA_INTELLIGENCE_HISTORY_PATH" in NOTES


def test_documented_error_codes_match_code():
    import inspect
    import intelligence_workspace_service as wss
    assert "SNAPSHOT_NOT_FOUND" in inspect.getsource(wss)
    assert "SNAPSHOT_NOT_FOUND" in NOTES
    assert "INVALID_PARAMETER" in inspect.getsource(flask_app)
    assert "INVALID_PARAMETER" in NOTES
    assert "İşlem tamamlanamadı" in NOTES


def test_export_format_docs_match_code():
    import workspace_export_api as wsx
    assert wsx.FORMATS == ("json", "csv")
    for claim in ("format=json", "UTF-8 BOM", "CRLF",
                  "Content-Disposition", "nosniff",
                  "formül enjeksiyon", "no-store, private"):
        assert claim.lower() in NOTES.lower(), claim


def test_test_history_numbers_consistent():
    for n in ("805", "829", "855", "873", "896", "916", "961", "969"):
        assert n in NOTES, n
    assert "969 PASS / 0 FAIL / 0 SKIP" in NOTES
    assert "164" in INDEX and "969" in INDEX
    assert "969" in TESTPROG


def test_mission_closed_with_pass_evidence():
    assert "MISSION 1500.2 — CLOSED" in NOTES
    closed_idx = NOTES.index("MISSION 1500.2 — CLOSED\n\n- Final Tests")
    tail = NOTES[closed_idx:]
    assert "969 PASS / 0 FAIL / 0 SKIP" in tail
    assert "Exchange Write: **0**" in tail
    assert "Secret Exposure: **0**" in tail


def test_no_secrets_in_docs():
    for pat in (r"BINANCE_[A-Z_]*=\S", r"sk-[A-Za-z0-9]{20}",
                r"AKIA[0-9A-Z]{16}", r"BEGIN [A-Z ]*PRIVATE KEY",
                r"eyJ[A-Za-z0-9_-]{20}"):
        assert not re.search(pat, ALL_DOCS), pat
    assert "password_hash" not in ALL_DOCS.lower().replace(
        "alpha_owner_password_hash", "")


def test_known_limitations_documented():
    for claim in ("henüz beslenmiyor", "indeks tabanlı",
                  "forecast: null", "yer tutucu", "MAX_RECORDS"):
        assert claim.lower() in NOTES.lower(), claim
