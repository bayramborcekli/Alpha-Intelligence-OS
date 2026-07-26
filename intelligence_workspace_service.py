"""Workspace Service Layer — Mission 1500.2 / Agent 03.

Timeline (intelligence_timeline) üzerinde SALT-OKUNUR servis
orkestrasyonu: zaman çizelgesi, tek kayıt, karşılaştırma, tavsiye
geçmişi, risk evrimi ve arama.

Kurallar:
- Yalnızca geçmiş kayıtlarından okur; hiçbir kaydı değiştirmez/silmez.
- Deterministik: aynı geçmiş → aynı çıktı, aynı sıralama.
- Tahmin/trend tahmini/otomatik karar YOKTUR; yalnızca kayıtlı veriler.
- Decimal değerler timeline'da string olarak durur ve aynen korunur.
- Bilinmeyen değer null/"—"/"Veri Yok" ile temsil edilir; asla türetilmez.
- Sterile hata: ham exception/stack trace/secret çıktıya taşınmaz.
"""

from __future__ import annotations

from typing import Any

import intelligence_timeline as timeline

VERI_YOK = "Veri Yok"

_COMPARE_FIELDS = tuple(
    f for f in timeline.ALLOWED_FIELDS if f != "generated_at"
)


def _sterile(code: str) -> dict:
    return {"ok": False, "read_only": True, "advisory_only": True,
            "error": {"code": code, "message": "İşlem tamamlanamadı"}}


def _envelope(payload: dict) -> dict:
    out = {"ok": True, "read_only": True, "advisory_only": True}
    out.update(payload)
    return out


def _records(path=None) -> list[dict]:
    return timeline.load_history(path)


def _entry(idx: int, rec: dict) -> dict:
    """Zaman çizelgesi için hafif kayıt özeti (1-tabanlı id)."""
    def _count(key: str) -> int | None:
        v = rec.get(key)
        return len(v) if isinstance(v, list) else None

    return {
        "id": idx,
        "generated_at": rec.get("generated_at"),
        "status": rec.get("status"),
        "partial": rec.get("partial"),
        "insight_count": _count("insights"),
        "recommendation_count": _count("recommendations"),
        "warning_count": _count("warnings"),
        "advisory_only": True,
    }


# ── Timeline / snapshot ──────────────────────────────────────────────

def get_timeline(limit: int | None = None, offset: int = 0,
                 path=None) -> dict:
    """Kayıt sırasına göre (eski → yeni) hafif zaman çizelgesi."""
    try:
        recs = _records(path)
        entries = [_entry(i + 1, r) for i, r in enumerate(recs)]
        total = len(entries)
        if offset:
            entries = entries[offset:] if offset > 0 else entries
        if limit is not None and limit >= 0:
            entries = entries[:limit]
        return _envelope({"total": total, "offset": max(offset, 0),
                          "entries": entries})
    except Exception:
        return _sterile("WORKSPACE_TIMELINE_ERROR")


def get_snapshot(snapshot_id: int, path=None) -> dict:
    """1-tabanlı id ile tek geçmiş kaydı; yoksa NOT_FOUND."""
    try:
        recs = _records(path)
        if not isinstance(snapshot_id, int) or isinstance(snapshot_id, bool) \
                or snapshot_id < 1 or snapshot_id > len(recs):
            return _sterile("SNAPSHOT_NOT_FOUND")
        rec = recs[snapshot_id - 1]
        return _envelope({"id": snapshot_id, "snapshot": rec})
    except Exception:
        return _sterile("WORKSPACE_SNAPSHOT_ERROR")


# ── Karşılaştırma ────────────────────────────────────────────────────

def _diff(a: Any, b: Any, trail: str, out: list[dict]) -> None:
    """Kayıtlı değerler üzerinde derin, deterministik fark çıkarımı."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            t = f"{trail}.{key}" if trail else str(key)
            if key not in a:
                out.append({"field": t, "change": "NEW",
                            "a": VERI_YOK, "b": b[key]})
            elif key not in b:
                out.append({"field": t, "change": "REMOVED",
                            "a": a[key], "b": VERI_YOK})
            else:
                _diff(a[key], b[key], t, out)
        return
    if isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            t = f"{trail}[{i}]"
            if i >= len(a):
                out.append({"field": t, "change": "NEW",
                            "a": VERI_YOK, "b": b[i]})
            elif i >= len(b):
                out.append({"field": t, "change": "REMOVED",
                            "a": a[i], "b": VERI_YOK})
            else:
                _diff(a[i], b[i], t, out)
        return
    if a != b:
        out.append({"field": trail, "change": "CHANGED", "a": a, "b": b})


def compare_snapshots(id_a: int, id_b: int, path=None) -> dict:
    """İki kaydı YALNIZCA kayıtlı alanlar üzerinden karşılaştırır.

    Yeni/Değişen/Kaldırılan alanlar açıkça listelenir; kayıtta olmayan
    taraf "Veri Yok" işaretlenir. Hiçbir değer türetilmez.
    """
    try:
        sa = get_snapshot(id_a, path)
        sb = get_snapshot(id_b, path)
        if not sa.get("ok") or not sb.get("ok"):
            return _sterile("SNAPSHOT_NOT_FOUND")
        ra, rb = sa["snapshot"], sb["snapshot"]
        differences: list[dict] = []
        for field in _COMPARE_FIELDS:
            va, vb = ra.get(field), rb.get(field)
            if va is None and vb is None:
                continue
            _diff(va, vb, field, differences)
        return _envelope({
            "a": {"id": id_a, "generated_at": ra.get("generated_at")},
            "b": {"id": id_b, "generated_at": rb.get("generated_at")},
            "compared_fields": list(_COMPARE_FIELDS),
            "differences": differences,
            "identical": not differences,
        })
    except Exception:
        return _sterile("WORKSPACE_COMPARE_ERROR")


# ── Tavsiye geçmişi ──────────────────────────────────────────────────

def get_recommendation_history(path=None) -> dict:
    """Tavsiyeleri code bazında gruplar; confidence/priority değişimini
    gösterir; ardışık tekrarları birleştirir (occurrence sayısıyla)."""
    try:
        groups: dict[str, dict] = {}
        for idx, rec in enumerate(_records(path), start=1):
            recos = rec.get("recommendations")
            if not isinstance(recos, list):
                continue
            for r in recos:
                if not isinstance(r, dict):
                    continue
                code = r.get("code") or "—"
                g = groups.setdefault(code, {"code": code, "history": []})
                point = {
                    "snapshot_id": idx,
                    "generated_at": rec.get("generated_at"),
                    "confidence": r.get("confidence"),
                    "priority": r.get("priority"),
                    "count": 1,
                }
                hist = g["history"]
                if hist and hist[-1]["confidence"] == point["confidence"] \
                        and hist[-1]["priority"] == point["priority"]:
                    hist[-1]["count"] += 1
                    hist[-1]["last_snapshot_id"] = idx
                    hist[-1]["last_generated_at"] = rec.get("generated_at")
                else:
                    hist.append(point)
        items = []
        for code in sorted(groups, key=str):
            g = groups[code]
            confs = [h["confidence"] for h in g["history"]]
            prios = [h["priority"] for h in g["history"]]
            items.append({
                "code": code,
                "occurrences": sum(h["count"] for h in g["history"]),
                "confidence_changed": len(set(map(str, confs))) > 1,
                "priority_changed": len(set(map(str, prios))) > 1,
                "history": g["history"],
            })
        return _envelope({"items": items})
    except Exception:
        return _sterile("WORKSPACE_RECOMMENDATION_ERROR")


# ── Risk evrimi ──────────────────────────────────────────────────────

def get_risk_evolution(path=None) -> dict:
    """Yalnızca geçmiş kayıtlarındaki risk verisinden zaman serisi.

    Tahmin veya trend kestirimi YAPILMAZ; eksik alanlar null kalır.
    """
    try:
        series = []
        for idx, rec in enumerate(_records(path), start=1):
            rs = rec.get("risk_summary")
            rs = rs if isinstance(rs, dict) else {}
            series.append({
                "snapshot_id": idx,
                "generated_at": rec.get("generated_at"),
                "risk_score": rs.get("score", None),
                "risk_status": rs.get("status", None),
                "risk_factors": rs.get("components", rs.get("factors")),
                "freshness": rec.get("freshness"),
            })
        return _envelope({"series": series, "forecast": None})
    except Exception:
        return _sterile("WORKSPACE_RISK_ERROR")


# ── Arama ────────────────────────────────────────────────────────────

def _codes(rec: dict, key: str) -> set:
    items = rec.get(key)
    if not isinstance(items, list):
        return set()
    return {i.get("code") for i in items if isinstance(i, dict)}


def _confidences(rec: dict) -> set:
    out = set()
    for key in ("insights", "recommendations"):
        items = rec.get(key)
        if isinstance(items, list):
            out |= {i.get("confidence") for i in items
                    if isinstance(i, dict)}
    return out


def search(start: str | None = None, end: str | None = None,
           status: str | None = None, confidence: str | None = None,
           recommendation_code: str | None = None,
           insight_code: str | None = None,
           partial: bool | None = None,
           advisory_only: bool | None = None,
           path=None) -> dict:
    """Kayıtlı alanlar üzerinde deterministik filtreli arama."""
    try:
        recs = _records(path)
        time_ok: set[int] | None = None
        if start is not None or end is not None:
            # İndeks-tabanlı eşleme: değer-eşit kopya kayıtlarla karışmaz.
            time_ok = set()
            for idx, rec in enumerate(recs, start=1):
                ts = timeline._parse_iso(rec.get("generated_at"))
                s = timeline._parse_iso(start)
                e = timeline._parse_iso(end)
                if ts is None:
                    continue
                if s is not None and ts < s:
                    continue
                if e is not None and ts > e:
                    continue
                time_ok.add(idx)
        out = []
        for idx, rec in enumerate(recs, start=1):
            if time_ok is not None and idx not in time_ok:
                continue
            if status is not None and rec.get("status") != status:
                continue
            if partial is not None and rec.get("partial") != partial:
                continue
            if advisory_only is not None and \
                    rec.get("advisory_only") != advisory_only:
                continue
            if confidence is not None and \
                    confidence not in _confidences(rec):
                continue
            if recommendation_code is not None and \
                    recommendation_code not in _codes(
                        rec, "recommendations"):
                continue
            if insight_code is not None and \
                    insight_code not in _codes(rec, "insights"):
                continue
            out.append(_entry(idx, rec))
        return _envelope({"total": len(out), "entries": out})
    except Exception:
        return _sterile("WORKSPACE_SEARCH_ERROR")
