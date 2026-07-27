"""Mission 1700 / Agent 06 — Portfolio Intelligence Export katmanı.

Salt-okunur dışa aktarım: veri YALNIZCA mevcut normalize PortfolioAnalysis
zarfından gelir (Agent 03 servis → Agent 02 çekirdek). Bu modül zarfı
üretmez ve DEĞİŞTİRMEZ; alternatif veri yolu yoktur. Hesap yapılmaz,
snapshot yazılmaz, dosya sistemi kullanılmaz (yalnız bellek içi üretim),
Exchange erişimi yoktur, zaman damgası üretilmez (generated_at zarftan
olduğu gibi taşınır).

Formatlar (Mission 1700 resmi): JSON ve CSV. JSON, zarfın deterministik
bayt temsilidir; alan adları, null'lar ve sabit-nokta string'ler aynen
korunur. CSV düzleştirilmiş rapor biçimidir: bilinmeyen değerler BOŞ
hücre kalır (asla 0 türetilmez), kolon/satır sırası deterministiktir,
formül enjeksiyonu nötralize edilir. Çıktı UTF-8'dir.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

FORMATS = ("json", "csv")

JSON_MIME = "application/json; charset=utf-8"
CSV_MIME = "text/csv; charset=utf-8"

JSON_FILENAME = "portfolio_intelligence.json"
CSV_FILENAME = "portfolio_intelligence.csv"

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

CSV_HEADER = ["section", "field", "value"]

# Sabit satır planı — CSV satır sırası bundan türetilir (deterministik).
_SUMMARY_FIELDS = (
    ("status", ("status",)),
    ("health", ("portfolio", "health", "portfolio_health_score")),
    ("nav", ("portfolio", "equity", "nav_usdt")),
    ("cash", ("portfolio", "equity", "cash_usdt")),
    ("gross_exposure", ("portfolio", "exposure", "gross")),
    ("net_exposure", ("portfolio", "exposure", "net")),
)
_POSITION_FIELDS = ("symbol", "side", "quantity", "mark_price",
                    "weight_pct", "unrealized_pnl")
_RISK_FIELDS = (
    ("net_exposure_util_pct",
     ("portfolio", "risk_utilization", "net_exposure_util_pct")),
    ("drawdown_util_pct",
     ("portfolio", "risk_utilization", "drawdown_util_pct")),
    ("concentration_util_pct",
     ("portfolio", "risk_utilization", "concentration_util_pct")),
)
_DIVERSIFICATION_FIELDS = (
    ("hhi", ("portfolio", "concentration", "hhi")),
    ("effective_positions",
     ("portfolio", "concentration", "effective_positions")),
    ("top_position", ("portfolio", "concentration", "top_symbol")),
    ("top_weight", ("portfolio", "concentration", "top_share_pct")),
)
_META_FIELDS = (
    ("analysis_version", ("analysis_version",)),
    ("generated_at", ("generated_at",)),
    ("read_only", ("read_only",)),
    ("advisory_only", ("advisory_only",)),
)


# ── Yardımcılar (yalnız taşıma/biçim — hesap YOK) ────────────────────

def _get(env: Any, path: tuple) -> Any:
    node = env
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _cell(v: Any) -> str:
    """Düz metin hücre: bilinmeyen → BOŞ; formül enjeksiyonu nötralize."""
    if v is None or v == "":
        return ""
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


def _csv_bytes(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    for r in rows:
        w.writerow(r)
    # UTF-8 BOM: Türkçe Excel uyumluluğu (Mission 1500.2/1600 standardı)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _json_bytes(envelope: dict) -> bytes:
    # Deterministik bayt temsili: sabit anahtar sırası, UTF-8, yeniden
    # yapılandırma YOK — zarf olduğu gibi serileştirilir.
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                      indent=2).encode("utf-8")


def _csv_rows(envelope: dict) -> list[list[str]]:
    rows: list[list[str]] = []
    for name, path in _META_FIELDS:
        rows.append(["meta", name, _cell(_get(envelope, path))])
    for name, path in _SUMMARY_FIELDS:
        rows.append(["summary", name, _cell(_get(envelope, path))])
    positions = _get(envelope, ("portfolio", "positions"))
    if isinstance(positions, list):
        for idx, pos in enumerate(positions, start=1):
            row_source = pos if isinstance(pos, dict) else {}
            for field in _POSITION_FIELDS:
                rows.append(["positions", f"{idx}.{field}",
                             _cell(row_source.get(field))])
    for name, path in _RISK_FIELDS:
        rows.append(["risk", name, _cell(_get(envelope, path))])
    breaches = _get(envelope, ("portfolio", "risk_utilization",
                               "limits_breached"))
    rows.append(["risk", "violations",
                 _cell("|".join(breaches)) if isinstance(breaches, list)
                 else _cell(breaches)])
    for name, path in _DIVERSIFICATION_FIELDS:
        rows.append(["diversification", name,
                     _cell(_get(envelope, path))])
    sources = envelope.get("sources")
    if isinstance(sources, dict):
        for src_name in sorted(sources):
            meta = sources[src_name]
            meta = meta if isinstance(meta, dict) else {}
            for field in ("status", "freshness", "available", "code"):
                rows.append(["sources", f"{src_name}.{field}",
                             _cell(meta.get(field))])
    return rows


# ── Export üreticisi ────────────────────────────────────────────────
# Sözleşme (Mission 1600 export deseniyle aynı):
# (envelope, body|None, mime|None, filename|None) döner. Geçersiz
# format/zarf → sterile hata zarfı, gövde üretilmez.

def export_analysis(envelope: Any, fmt: str):
    if fmt not in FORMATS:
        return ({"ok": False, "error": {
            "code": "INVALID_FORMAT",
            "message": "Geçersiz format parametresi."}},
            None, None, None)
    if not isinstance(envelope, dict) or "status" not in envelope:
        return ({"ok": False, "error": {
            "code": "ANALYSIS_UNAVAILABLE",
            "message": "Portföy analizi dışa aktarılamadı."}},
            None, None, None)
    if fmt == "json":
        return envelope, _json_bytes(envelope), JSON_MIME, JSON_FILENAME
    return envelope, _csv_bytes(_csv_rows(envelope)), CSV_MIME, \
        CSV_FILENAME
