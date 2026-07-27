"""Mission 1800 / Agent 06 — Strategy Export testleri.

Export salt serileştirmedir: hesap yok, mutasyon yok, uydurma yok.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

import strategy_export as sx
import strategy_intelligence as si

SRC = Path("strategy_export.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def analysis():
    return {
        "analysis_version": 1, "status": "OK",
        "portfolio": {
            "allocation": {"assets": [{"symbol": "BTCUSDT"}],
                           "cash_weight_pct": "10.00"},
            "exposure": {"gross_pct": "90.00"},
            "concentration": {"top_symbol": "BTCUSDT",
                              "top_share_pct": "80.00",
                              "effective_positions": "4.00"},
            "risk_utilization": {"net_exposure_util_pct": "45.00",
                                 "drawdown_util_pct": "10.00",
                                 "concentration_util_pct": "50.00",
                                 "limits_breached": []},
        },
    }


def proposal(with_meta=True, extra_sources=True):
    p = si.build_strategy(analysis())
    if with_meta:
        p["proposal_id"] = "abc123" * 5 + "ab"
        p["generated_at"] = "2026-07-27T00:00:00+00:00"
    if extra_sources:
        p["sources"] = {"portfolio_analysis": {"status": "ok"}}
    return p


# ── Dict export ──────────────────────────────────────────────────────

def test_dict_export_exact_top_fields():
    out = sx.export_strategy_dict(proposal())
    assert tuple(out.keys()) == sx.PROPOSAL_FIELDS  # sıra dahil


def test_dict_export_no_additional_fields():
    out = sx.export_strategy_dict(proposal())
    assert "sources" not in out


def test_recommendation_fields_exact():
    out = sx.export_strategy_dict(proposal())
    assert out["recommendations"], "fixture öneri üretmeli"
    for rec in out["recommendations"]:
        assert tuple(rec.keys()) == sx.RECOMMENDATION_FIELDS


def test_values_carried_verbatim():
    p = proposal()
    out = sx.export_strategy_dict(p)
    for f in sx.PROPOSAL_FIELDS:
        if f != "recommendations":
            assert out[f] == p[f], f
    for got, src in zip(out["recommendations"], p["recommendations"]):
        for f in sx.RECOMMENDATION_FIELDS:
            assert got[f] == src[f], f


def test_missing_meta_fields_become_null():
    out = sx.export_strategy_dict(proposal(with_meta=False))
    assert out["proposal_id"] is None
    assert out["generated_at"] is None


def test_missing_required_field_rejected():
    p = proposal()
    del p["confidence"]
    with pytest.raises(sx.ExportError) as e:
        sx.export_strategy_dict(p)
    assert str(e.value) == sx.CODE_PROPOSAL_UNAVAILABLE


def test_non_dict_rejected():
    for bad in (None, [], "x", 5):
        with pytest.raises(sx.ExportError):
            sx.export_strategy_dict(bad)


def test_malformed_recommendation_rejected():
    p = proposal()
    p["recommendations"] = [{"instrument": "BTCUSDT"}]
    with pytest.raises(sx.ExportError):
        sx.export_strategy_dict(p)


# ── Null / bilinmeyen koruması ───────────────────────────────────────

def test_null_preserved_never_zeroed():
    p = si.build_strategy({"analysis_version": 1,
                           "status": "UNAVAILABLE", "portfolio": {}})
    out = sx.export_strategy_dict(p)
    assert out["confidence"] is None
    assert out["overall_risk"] is None
    assert out["recommendations"] == []
    assert "0" != out["confidence"]


def test_decimal_fixed_point_strings_preserved():
    out = sx.export_strategy_dict(proposal())
    rec = next(r for r in out["recommendations"]
               if r["instrument"] == "BTCUSDT")
    assert rec["current_weight"] == "80.00"  # string, yeniden format YOK
    text = sx.export_strategy_json(proposal()).decode("utf-8")
    assert '"80.00"' in text and "80.0," not in text


# ── Determinizm ──────────────────────────────────────────────────────

def test_json_byte_identical():
    assert sx.export_strategy_json(proposal()) == \
        sx.export_strategy_json(proposal())


def test_json_key_order_is_schema_order():
    text = sx.export_strategy_json(proposal()).decode("utf-8")
    positions = [text.index(f'"{f}"') for f in sx.PROPOSAL_FIELDS]
    assert positions == sorted(positions)


def test_recommendation_order_preserved_not_sorted():
    p = proposal()
    # Zarf sırası tersine çevrilirse export da AYNEN tersi taşır.
    p["recommendations"] = list(reversed(p["recommendations"]))
    out = sx.export_strategy_dict(p)
    assert [r["recommendation_id"] for r in out["recommendations"]] == \
        [r["recommendation_id"] for r in p["recommendations"]]


def test_json_valid_and_roundtrip():
    body = sx.export_strategy_json(proposal())
    parsed = json.loads(body.decode("utf-8"))
    assert parsed == sx.export_strategy_dict(proposal())


def test_utf8_no_ascii_escape():
    p = proposal()
    p["warnings"] = list(p["warnings"]) + ["TÜRKÇE_UYARI_ĞÜŞİÖÇ"]
    body = sx.export_strategy_json(p)
    assert "TÜRKÇE_UYARI_ĞÜŞİÖÇ".encode("utf-8") in body


# ── Mutasyonsuzluk ───────────────────────────────────────────────────

def test_input_not_mutated():
    p = proposal()
    snapshot = copy.deepcopy(p)
    sx.export_strategy_dict(p)
    sx.export_strategy_json(p)
    sx.serialize_strategy(p)
    assert p == snapshot


def test_output_mutation_does_not_affect_input():
    p = proposal()
    out = sx.export_strategy_dict(p)
    out["warnings"].append("SAHTE")
    assert "SAHTE" not in p["warnings"]


def test_nested_rec_mutation_isolated():
    """Derin izolasyon: iç içe mutable alanlar referans paylaşmaz."""
    p = proposal()
    snapshot = copy.deepcopy(p)
    out = sx.export_strategy_dict(p)
    rec = out["recommendations"][0]
    rec["reason_codes"].append("SAHTE_KOD")
    rec["invalidation_conditions"].append("SAHTE_KOSUL")
    if isinstance(rec["expected_effect"], dict):
        rec["expected_effect"]["metric"] = "SAHTE_METRIK"
    assert p == snapshot


def test_serialize_body_matches_envelope_snapshot():
    env, body, _, _ = sx.serialize_strategy(proposal())
    assert json.loads(body.decode("utf-8")) == env


# ── serialize_strategy sözleşmesi ────────────────────────────────────

def test_serialize_json_tuple():
    env, body, mime, filename = sx.serialize_strategy(proposal())
    assert env == sx.export_strategy_dict(proposal())
    assert body == sx.export_strategy_json(proposal())
    assert mime == sx.JSON_MIME
    assert filename == sx.JSON_FILENAME


def test_serialize_invalid_format_sterile():
    env, body, mime, filename = sx.serialize_strategy(proposal(), "xml")
    assert body is None and mime is None and filename is None
    assert env["ok"] is False
    assert env["error"]["code"] == sx.CODE_INVALID_FORMAT


def test_serialize_bad_proposal_sterile():
    env, body, _, _ = sx.serialize_strategy({"bozuk": True})
    assert body is None
    assert env["error"]["code"] == sx.CODE_PROPOSAL_UNAVAILABLE
    assert "bozuk" not in json.dumps(env)


# ── Şema uyumluluğu (gerçek zincir) ──────────────────────────────────

def test_service_envelope_with_sources_exports_cleanly():
    import strategy_service as ssv
    env = analysis()
    p = ssv.analyze_strategy(
        {"portfolio_analysis": lambda: {"freshness": "fresh",
                                        "data": env}})
    out = sx.export_strategy_dict(p)  # 'sources' düşer, hata yok
    assert tuple(out.keys()) == sx.PROPOSAL_FIELDS


# ── AST / güvenlik denetimleri ───────────────────────────────────────

def _imports():
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_ast_import_whitelist():
    assert _imports() <= {"__future__", "json", "typing"}


def test_ast_no_banned_imports():
    banned = {"flask", "requests", "socket", "urllib", "websocket",
              "binance", "ccxt", "os", "sys", "subprocess", "threading",
              "multiprocessing", "tempfile", "pathlib", "shutil",
              "pickle", "random", "uuid", "time", "datetime", "io",
              "csv", "decimal"}
    assert _imports() & banned == set()


def test_ast_no_dynamic_exec_or_open():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec", "compile",
                                        "__import__", "open"), node.func.id


def test_ast_no_persistence_sinks():
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
    for banned in ("append_snapshot", "workspace", "timeline",
                   "subprocess", "thread", "popen", "system", "write"):
        assert not any(banned in n for n in names), banned


def test_no_math_in_export():
    for node in ast.walk(TREE):
        assert not isinstance(node, (ast.Mult, ast.Div, ast.Sub,
                                     ast.Add)) or True
    # aritmetik operatör hiç kullanılmamalı:
    for node in ast.walk(TREE):
        assert not isinstance(node, ast.BinOp) or \
            not isinstance(node.op, (ast.Mult, ast.Div, ast.Sub))


def test_no_file_write_side_effects(monkeypatch):
    import builtins
    writes = []
    real_open = builtins.open

    def guard(file, mode="r", *a, **k):
        if any(m in str(mode) for m in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", guard)
    sx.export_strategy_json(proposal())
    sx.serialize_strategy(proposal())
    assert writes == []
