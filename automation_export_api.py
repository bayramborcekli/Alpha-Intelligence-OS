"""Mission 1600 / Agent 06 — Automation Export katmanı.

Salt-okunur dışa aktarım: veri YALNIZCA automation_engine'in mevcut durum
okuma sözleşmesinden (load_config/load_state) alınır. Hiçbir koşu
başlatılmaz, snapshot yazılmaz, Intelligence çağrılmaz, Exchange erişimi
yapılmaz.

Formatlar (Mission 1500.2 export standardı aynen): JSON ve CSV, seçim
`?format=` sorgu parametresiyle. CSV formül-enjeksiyon korumalı; bilinmeyen
değerler "—" olarak gösterilir, asla 0 türetilmez. Çıktı deterministiktir.

Çalışma geçmişi (history) modeli repository'de YOKTUR (durum dosyası yalnız
son koşuyu tutar); history export bilinçli olarak eklenmemiştir.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

import automation_engine

FORMATS = ("json", "csv")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

JSON_MIME = "application/json; charset=utf-8"
CSV_MIME = "text/csv; charset=utf-8"

# Beyaz-listed sterile alanlar — gerçek status API sözleşmesiyle birebir.
# Sıra sabittir: CSV kolon sırası ve JSON alan kümesi bundan türetilir.
STATUS_FIELDS = (
    "enabled",
    "interval_minutes",
    "state",
    "running",
    "run_id",
    "last_run_started_at",
    "last_run_finished_at",
    "last_run_status",
    "last_error_code",
    "last_snapshot_recorded",
    "next_due",
)


# ── CSV yardımcıları (Mission 1500.2 deseniyle aynı) ────────────────

def _cell(v: Any) -> str:
    """Düz metin hücre: bilinmeyen → "—"; formül enjeksiyonu nötralize."""
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


# ── Durum okuma (yalnız mevcut sözleşme) ────────────────────────────

def build_status() -> dict:
    """Status API ile aynı sterile görünümü üretir; hata → sterile zarf.

    Yalnız automation_engine.load_config/load_state okunur. Alan kümesi
    STATUS_FIELDS beyaz listesiyle sınırlıdır; yol, PID, iç ayrıntı yoktur.
    """
    try:
        cfg = automation_engine.load_config()
        st = automation_engine.load_state()
        next_due = None
        if cfg["enabled"] and st.get("last_run_finished_at"):
            epoch = automation_engine._epoch_of(st["last_run_finished_at"])
            if epoch is not None:
                next_due = datetime.fromtimestamp(
                    epoch + cfg["interval_minutes"] * 60,
                    timezone.utc).isoformat()
        view = {
            "enabled": cfg["enabled"],
            "interval_minutes": cfg["interval_minutes"],
            "state": st["state"],
            "running": st["state"] == "running",
            "run_id": st["run_id"],
            "last_run_started_at": st["last_run_started_at"],
            "last_run_finished_at": st["last_run_finished_at"],
            "last_run_status": st["last_run_status"],
            "last_error_code": st["last_error_code"],
            "last_snapshot_recorded": st["last_snapshot_recorded"],
            "next_due": next_due,
        }
    except Exception:
        return {"ok": False, "error": {
            "code": "STATUS_UNAVAILABLE",
            "message": "Automation durumu okunamadı."}}
    return {"ok": True, "read_only": True, "advisory_only": True,
            "status": view}


# ── Export üreticisi ────────────────────────────────────────────────
# (envelope, body|None, mime|None, filename|None) döner. envelope ok
# değilse gövde üretilmez; HTTP katmanı sterile zarfı döner.

def export_status(fmt: str):
    env = build_status()
    if env.get("ok") is not True:
        return env, None, None, None
    if fmt == "json":
        return env, _json_bytes(env), JSON_MIME, "automation_status.json"
    header = ["field", "value"]
    view = env["status"]
    rows = [[_cell(k), _cell(view.get(k))] for k in STATUS_FIELDS]
    return env, _csv_bytes(header, rows), CSV_MIME, "automation_status.csv"
