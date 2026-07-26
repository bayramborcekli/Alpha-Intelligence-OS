"""Mission 1500.2 / Agent 06 — Workspace Export katmanı.

Salt-okunur dışa aktarım: veriler YALNIZCA intelligence_workspace_service
üzerinden alınır (timeline modülüne doğrudan erişim yoktur). Hiçbir kayıt
oluşturulmaz veya güncellenmez.

Formatlar: JSON (servis zarfı aynen, deterministik) ve CSV (yalnızca düz
metin alanları; formül-enjeksiyon korumalı). Decimal değerler string
olarak korunur; bilinmeyen değerler CSV'de "—" olarak gösterilir, asla 0
türetilmez.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import intelligence_workspace_service as wss

FORMATS = ("json", "csv")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

JSON_MIME = "application/json; charset=utf-8"
CSV_MIME = "text/csv; charset=utf-8"


# ── CSV yardımcıları ────────────────────────────────────────────────

def _cell(v: Any) -> str:
    """Düz metin hücre: bilinmeyen → "—"; formül enjeksiyonu nötralize.

    Sayılar/Decimal-string'ler değiştirilmez (yalnızca formül önekli
    METİNLER korunur; "-12.5" gibi sayısal string'ler sayı olarak kalır).
    """
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v)
    if s.startswith(_FORMULA_PREFIXES) and not _is_number(s):
        return "'" + s
    return s


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _flat(v: Any) -> str:
    """Yapısal değerler CSV'de kanonik JSON metnine düzleştirilir."""
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return _cell(json.dumps(v, ensure_ascii=False, sort_keys=True))
    return _cell(v)


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    # UTF-8 BOM: Türkçe Excel uyumluluğu
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _json_bytes(payload: dict) -> bytes:
    # Deterministik: anahtar sırası sabit
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      indent=2).encode("utf-8")


def _ok(env: dict) -> bool:
    return isinstance(env, dict) and env.get("ok") is True


# ── Export üreticileri ──────────────────────────────────────────────
# Her fonksiyon: (envelope, body|None, mime|None, filename|None) döner.
# envelope ok değilse gövde üretilmez; HTTP katmanı sterile zarfı döner.

def export_timeline(fmt: str, limit=None, offset=0):
    env = wss.get_timeline(limit=limit, offset=offset)
    if not _ok(env):
        return env, None, None, None
    if fmt == "json":
        return env, _json_bytes(env), JSON_MIME, "workspace_timeline.json"
    header = ["id", "generated_at", "status", "partial",
              "insight_count", "recommendation_count", "warning_count",
              "advisory_only"]
    rows = [[_cell(e.get(k)) for k in header]
            for e in env.get("entries", [])]
    return env, _csv_bytes(header, rows), CSV_MIME, "workspace_timeline.csv"


def export_snapshot(fmt: str, snapshot_id: int):
    env = wss.get_snapshot(snapshot_id)
    if not _ok(env):
        return env, None, None, None
    name = f"workspace_snapshot_{snapshot_id}"
    if fmt == "json":
        return env, _json_bytes(env), JSON_MIME, name + ".json"
    snap = env.get("snapshot") or {}
    header = ["field", "value"]
    rows = [[_cell(k), _flat(snap.get(k))] for k in sorted(snap)]
    return env, _csv_bytes(header, rows), CSV_MIME, name + ".csv"


def export_compare(fmt: str, id_a: int, id_b: int):
    env = wss.compare_snapshots(id_a, id_b)
    if not _ok(env):
        return env, None, None, None
    name = f"workspace_compare_{id_a}_{id_b}"
    if fmt == "json":
        return env, _json_bytes(env), JSON_MIME, name + ".json"
    # Deterministik alan sırası: servisin ürettiği sıra AYNEN korunur.
    header = ["field", "change", "a", "b"]
    rows = [[_cell(d.get("field")), _cell(d.get("change")),
             _flat(d.get("a")), _flat(d.get("b"))]
            for d in env.get("differences", [])]
    return env, _csv_bytes(header, rows), CSV_MIME, name + ".csv"


def export_recommendations(fmt: str):
    env = wss.get_recommendation_history()
    if not _ok(env):
        return env, None, None, None
    if fmt == "json":
        return env, _json_bytes(env), JSON_MIME, \
            "workspace_recommendations.json"
    header = ["code", "occurrences", "confidence_changed",
              "priority_changed", "history"]
    rows = [[_cell(i.get("code")), _cell(i.get("occurrences")),
             _cell(i.get("confidence_changed")),
             _cell(i.get("priority_changed")), _flat(i.get("history"))]
            for i in env.get("items", [])]
    return env, _csv_bytes(header, rows), CSV_MIME, \
        "workspace_recommendations.csv"


def export_risk_evolution(fmt: str):
    env = wss.get_risk_evolution()
    if not _ok(env):
        return env, None, None, None
    if fmt == "json":
        return env, _json_bytes(env), JSON_MIME, \
            "workspace_risk_evolution.json"
    header = ["snapshot_id", "generated_at", "risk_score", "risk_status",
              "risk_factors", "freshness"]
    rows = [[_cell(p.get("snapshot_id")), _cell(p.get("generated_at")),
             _cell(p.get("risk_score")), _cell(p.get("risk_status")),
             _flat(p.get("risk_factors")), _flat(p.get("freshness"))]
            for p in env.get("series", [])]
    return env, _csv_bytes(header, rows), CSV_MIME, \
        "workspace_risk_evolution.csv"


def export_search(fmt: str, **filters):
    env = wss.search(**filters)
    if not _ok(env):
        return env, None, None, None
    if fmt == "json":
        return env, _json_bytes(env), JSON_MIME, "workspace_search.json"
    header = ["id", "generated_at", "status", "partial",
              "insight_count", "recommendation_count", "warning_count",
              "advisory_only"]
    rows = [[_cell(e.get(k)) for k in header]
            for e in env.get("entries", [])]
    return env, _csv_bytes(header, rows), CSV_MIME, "workspace_search.csv"
