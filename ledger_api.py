"""Mission 1400.4 — Defter / Denetim / Rapor servis katmanı (salt okunur).

Kaynaklar:
- Defter: alpha20_v1/mission1310b/ledger_events.json (ekle-yalnız, 1310B)
- Mutabakat: alpha20_v1/mission1310b/mission_1310b_report.json (1310B kanıtı)
- Denetim: security_log.py'nin ürettiği security.log (+ rotasyon dosyaları)
- Raporlar: sabit kayıt defteri (registry) — kullanıcı dosya yolu ASLA kabul
  edilmez, yol geçişi (path traversal) mümkün değildir.

Hiçbir fonksiyon kaynak dosyaları DEĞİŞTİRMEZ; tüm erişim salt okunurdur.
Parasal alanlar Decimal-uyumlu string olarak taşınır (float yok).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from portfolio_api import (InvalidParameter, _parse_bool, _parse_enum,
                           _parse_search, _csv_text, _csv_num,
                           _csv_response_body, _stamp)

ROOT = Path(__file__).resolve().parent
LEDGER_PATH = ROOT / "alpha20_v1" / "mission1310b" / "ledger_events.json"
RECON_PATH = ROOT / "alpha20_v1" / "mission1310b" / "mission_1310b_report.json"
AUDIT_LOG_PATH = ROOT / "security.log"

LEDGER_DEFAULT_LIMIT = 50
LEDGER_MAX_LIMIT = 500
AUDIT_DEFAULT_LIMIT = 50
AUDIT_MAX_LIMIT = 500
AUDIT_MAX_LINES = 20000          # sınırsız dosya okuması yok
REPORTS_DEFAULT_LIMIT = 25
REPORTS_MAX_LIMIT = 100
INTEGRITY_FRESH_SECONDS = 15 * 60

NORMALIZED_TYPES = {"DEPOSIT", "WITHDRAWAL", "INTERNAL_TRANSFER", "SPOT_BUY",
                    "SPOT_SELL", "FUTURES_OPEN", "FUTURES_CLOSE", "FEE",
                    "FUNDING", "REALIZED_PNL", "UNKNOWN"}
LEDGER_SORTS = {"timestamp": True, "asset": False, "amount": True,
                "event_type": False, "exchange": False}

PARTIAL_WARNING_TR = ("Binance TR API geçmişi tüm spot işlemleri, "
                      "dönüşümleri ve hesap ömrü boyunca gerçekleşen tüm "
                      "hareketleri içermeyebilir.")

# ── mtime-anahtarlı güvenli önbellek ────────────────────────────────────────
_CACHE: dict[str, tuple[float, Any]] = {}


def invalidate_ledger_caches() -> list[str]:
    keys = list(_CACHE)
    _CACHE.clear()
    return keys


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _cached(key: str, path: Path, build):
    mt = _mtime(path)
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    val = build()
    _CACHE[key] = (mt, val)
    return val


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dec_str(v: Any, default: str = "0") -> str:
    """Decimal-uyumlu string döndür; bozuk değer → default (izole hata)."""
    if v is None:
        return default
    try:
        return str(Decimal(str(v)))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _mask_tx(tx: Any) -> str:
    s = str(tx or "")
    if len(s) <= 4:
        return "****" if s else ""
    return s[:2] + "…" + s[-2:]


# ── Defter yükleme ve tipli model ───────────────────────────────────────────

def _normalize_type(original: str) -> str:
    up = (original or "").strip().upper()
    return up if up in NORMALIZED_TYPES else "UNKNOWN"


def _load_ledger_raw() -> dict:
    """Ham defter kayıtlarını oku; ASLA yazma. Sonuç: parsed/malformed."""
    def build():
        out = {"ok": False, "events": [], "malformed": 0, "error": None}
        if not LEDGER_PATH.exists():
            out["error"] = "LEDGER_UNAVAILABLE"
            return out
        try:
            data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out["error"] = "LEDGER_UNAVAILABLE"
            return out
        if not isinstance(data, list):
            out["error"] = "MALFORMED_LEDGER_RECORD"
            return out
        recon = _load_recon_raw()
        recon_state = str(recon.get("ledger_reconciliation") or "UNKNOWN")
        for rec in data:
            if not isinstance(rec, dict) or not rec.get("event_id"):
                out["malformed"] += 1
                continue
            try:
                ts_ms = int(rec.get("timestamp_ms") or 0)
            except (ValueError, TypeError):
                ts_ms = 0
                out["malformed"] += 1
            original = str(rec.get("class") or "")
            amount = _dec_str(rec.get("amount"))
            fee = _dec_str(rec.get("fee"))
            try:
                net = str(Decimal(amount) - Decimal(fee))
            except InvalidOperation:
                net = ""
            out["events"].append({
                "event_id": str(rec.get("event_id")),
                "exchange": str(rec.get("exchange") or "UNKNOWN"),
                "original_event_type": original,
                "normalized_event_type": _normalize_type(original),
                "asset": str(rec.get("asset") or ""),
                "amount": amount,
                "fee": fee,
                "net_amount": net,
                "timestamp_ms": ts_ms,
                "timestamp_iso": str(rec.get("timestamp_iso") or ""),
                "source_transaction_id_masked": _mask_tx(rec.get("tx_id")),
                "raw_payload_hash": str(rec.get("raw_payload_sha256") or ""),
                "reconciliation_state": recon_state,
                "ingestion_time": str(recon.get("finished_at") or ""),
                "duplicate_status": "UNIQUE",
                "internal_transfer": _normalize_type(original)
                == "INTERNAL_TRANSFER",
                "network": str(rec.get("network") or ""),
                "source": "MISSION_1310B_LEDGER",
            })
        out["ok"] = True
        return out
    return _cached("ledger_raw", LEDGER_PATH, build)


def _canonical_order(events: list[dict]) -> list[dict]:
    """Deterministik kanonik sıra: (timestamp_ms, event_id)."""
    return sorted(events, key=lambda e: (e["timestamp_ms"], e["event_id"]))


def _load_recon_raw() -> dict:
    def build():
        try:
            d = json.loads(RECON_PATH.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return _cached("recon_raw", RECON_PATH, build)


# ── Bütünlük doğrulaması (salt okunur) ──────────────────────────────────────

def ledger_integrity() -> dict:
    def build():
        raw = _load_ledger_raw()
        warnings: list[str] = []
        if raw["error"]:
            # Okunamayan VEYA bozuk üst-yapı → sert FAIL (fail-closed)
            msg = ("Defter kaynağı okunamadı."
                   if raw["error"] == "LEDGER_UNAVAILABLE"
                   else "Defter kaynağı bozuk biçimde (liste değil).")
            return {"status": "FAIL", "checked_record_count": 0,
                    "duplicate_count": 0, "malformed_record_count": 0,
                    "hash_mismatch_count": 0, "ordering_status": "UNKNOWN",
                    "verified_at": _now_iso(),
                    "warnings": [msg],
                    "error": raw["error"]}
        events = raw["events"]
        ids = [e["event_id"] for e in events]
        dup_ids = len(ids) - len(set(ids))
        missing_hash = sum(1 for e in events if not e["raw_payload_hash"])
        canon = _canonical_order(events)
        ordering_ok = (_canonical_order(list(reversed(events))) == canon)
        if missing_hash:
            warnings.append(f"{missing_hash} kayıtta yük özeti (hash) yok.")
        warnings.append("Ham yük saklanmadığı için hash yeniden hesaplaması "
                        "desteklenmez; saklanan özetlerin varlığı doğrulandı.")
        if raw["malformed"]:
            warnings.append(f"{raw['malformed']} bozuk kayıt izole edildi.")
        status = "PASS"
        if dup_ids or not ordering_ok:
            status = "FAIL"
        elif raw["malformed"] or missing_hash:
            status = "PARTIAL"
        return {"status": status,
                "checked_record_count": len(events),
                "duplicate_count": dup_ids,
                "malformed_record_count": raw["malformed"],
                "hash_mismatch_count": 0,
                "hash_check": "PRESENCE_ONLY",
                "ordering_status": "DETERMINISTIC" if ordering_ok
                else "NON_DETERMINISTIC",
                "verified_at": _now_iso(),
                "warnings": warnings}
    return _cached("integrity", LEDGER_PATH, build)


def _ledger_freshness(integrity: dict) -> str:
    if integrity.get("status") == "FAIL" and integrity.get("error"):
        return "KULLANILAMIYOR"
    try:
        v = datetime.fromisoformat(integrity["verified_at"])
        age = (datetime.now(timezone.utc) - v).total_seconds()
        return "GÜNCEL" if age <= INTEGRITY_FRESH_SECONDS else "ESKİ VERİ"
    except (KeyError, ValueError):
        return "KULLANILAMIYOR"


# ── Defter olayları (filtre + sayfalama) ────────────────────────────────────

def _parse_date(val: str | None, name: str) -> str | None:
    if not val:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
        raise InvalidParameter(name)
    try:
        datetime.strptime(val, "%Y-%m-%d")
    except ValueError:
        raise InvalidParameter(name)
    return val


def parse_page(args, default: int, maximum: int) -> tuple[int, int]:
    lim_s, off_s = args.get("limit"), args.get("offset")
    try:
        limit = default if lim_s in (None, "") else int(lim_s)
        offset = 0 if off_s in (None, "") else int(off_s)
    except ValueError:
        raise InvalidParameter("pagination")
    if limit < 1 or limit > maximum or offset < 0 or offset > 1_000_000:
        raise InvalidParameter("pagination")
    return limit, offset


def parse_ledger_filters(args) -> dict:
    limit, offset = parse_page(args, LEDGER_DEFAULT_LIMIT, LEDGER_MAX_LIMIT)
    return {
        "exchange": _parse_enum(args.get("exchange"),
                                {"BINANCE_TR", "BINANCE_GLOBAL"}, "exchange"),
        "event_type": _parse_enum(args.get("event_type"), NORMALIZED_TYPES,
                                  "event_type"),
        "asset": _parse_search(args.get("asset")) or None,
        "reconciliation_state": _parse_enum(args.get("reconciliation_state"),
                                            {"PASS", "PARTIAL", "FAIL",
                                             "UNKNOWN"},
                                            "reconciliation_state"),
        "duplicate_status": _parse_enum(args.get("duplicate_status"),
                                        {"UNIQUE", "BLOCKED"},
                                        "duplicate_status"),
        "internal_transfer": (None if args.get("internal_transfer")
                              in (None, "") else
                              _parse_bool(args.get("internal_transfer"))),
        "date_from": _parse_date(args.get("date_from"), "date_from"),
        "date_to": _parse_date(args.get("date_to"), "date_to"),
        "search": _parse_search(args.get("search")),
        "sort": _parse_enum(args.get("sort"), set(LEDGER_SORTS), "sort",
                            "timestamp"),
        "order": _parse_enum(args.get("order"), {"asc", "desc"}, "order",
                             "desc"),
        "limit": limit, "offset": offset,
    }


def _filter_ledger(events: list[dict], f: dict) -> list[dict]:
    out = events
    if f.get("exchange"):
        out = [e for e in out if e["exchange"] == f["exchange"]]
    if f.get("event_type"):
        out = [e for e in out if e["normalized_event_type"] == f["event_type"]]
    if f.get("asset"):
        out = [e for e in out if e["asset"].upper() == f["asset"]]
    if f.get("reconciliation_state"):
        out = [e for e in out
               if e["reconciliation_state"] == f["reconciliation_state"]]
    if f.get("duplicate_status"):
        out = [e for e in out
               if e["duplicate_status"] == f["duplicate_status"]]
    if f.get("internal_transfer") is not None:
        out = [e for e in out
               if e["internal_transfer"] is f["internal_transfer"]]
    if f.get("date_from"):
        out = [e for e in out if e["timestamp_iso"][:10] >= f["date_from"]]
    if f.get("date_to"):
        out = [e for e in out if e["timestamp_iso"][:10] <= f["date_to"]]
    if f.get("search"):
        s = f["search"]
        out = [e for e in out if s in e["asset"].upper()
               or s in e["event_id"].upper()
               or s in e["normalized_event_type"]
               or s in e["original_event_type"].upper()]
    sort = f.get("sort") or "timestamp"
    desc = (f.get("order") or "desc") == "desc"
    if sort == "timestamp":
        out = sorted(out, key=lambda e: (e["timestamp_ms"], e["event_id"]),
                     reverse=desc)
    elif LEDGER_SORTS[sort]:
        out = sorted(out, key=lambda e: Decimal(e["amount"] or "0"),
                     reverse=desc)
    elif sort == "event_type":
        out = sorted(out, key=lambda e: e["normalized_event_type"],
                     reverse=desc)
    else:
        out = sorted(out, key=lambda e: e[sort], reverse=desc)
    return out


def ledger_events(f: dict) -> dict:
    raw = _load_ledger_raw()
    integ = ledger_integrity()
    if raw["error"]:
        return {"ok": False, "error": {"code": raw["error"],
                "message": "Defter kaynağı okunamıyor veya bozuk."},
                "events": [], "pagination": {"total": 0, "limit": f["limit"],
                                             "offset": f["offset"],
                                             "has_more": False},
                "freshness": "KULLANILAMIYOR"}
    rows = _filter_ledger(raw["events"], f)
    total = len(rows)
    page = rows[f["offset"]:f["offset"] + f["limit"]]
    return {"ok": True, "events": page,
            "pagination": {"total": total, "limit": f["limit"],
                           "offset": f["offset"],
                           "has_more": f["offset"] + f["limit"] < total},
            "integrity_status": integ["status"],
            "freshness": _ledger_freshness(integ),
            "read_only": True}


def ledger_summary() -> dict:
    raw = _load_ledger_raw()
    integ = ledger_integrity()
    recon = _load_recon_raw()
    warnings: list[str] = []
    recon_status = str(recon.get("ledger_reconciliation") or "UNKNOWN")
    if recon_status == "PARTIAL":
        warnings.append(PARTIAL_WARNING_TR)
    if raw["error"]:
        warnings.append("Defter kaynağı okunamadı; dışa aktarma kapalı.")
    ev = raw["events"]
    ntypes = [e["normalized_event_type"] for e in ev]
    tss = sorted(e["timestamp_iso"] for e in ev if e["timestamp_iso"])
    return {
        "ok": not raw["error"],
        "total_event_count": len(ev),
        "unique_event_count": len({e["event_id"] for e in ev}),
        "duplicate_blocked_count": int(recon.get("duplicates_blocked") or 0),
        "unknown_event_count": ntypes.count("UNKNOWN"),
        "deposit_count": ntypes.count("DEPOSIT"),
        "withdrawal_count": ntypes.count("WITHDRAWAL"),
        "internal_transfer_count": ntypes.count("INTERNAL_TRANSFER"),
        "earliest_event": tss[0] if tss else None,
        "latest_event": tss[-1] if tss else None,
        "asset_count": len({e["asset"] for e in ev}),
        "exchange_count": len({e["exchange"] for e in ev}),
        "reconciliation_status": recon_status,
        "integrity_status": integ["status"],
        "last_integrity_verification": integ["verified_at"],
        "source_freshness": _ledger_freshness(integ),
        "append_only": True,
        "warnings": warnings,
    }


def ledger_reconciliation() -> dict:
    recon = _load_recon_raw()
    if not recon:
        return {"ok": False, "status": "UNKNOWN",
                "error": {"code": "RECONCILIATION_PARTIAL",
                          "message": "Mutabakat kanıtı bulunamadı."},
                "warnings": ["1310B mutabakat kanıt dosyası okunamadı."]}
    status = str(recon.get("ledger_reconciliation") or "UNKNOWN")
    warnings = [PARTIAL_WARNING_TR] if status == "PARTIAL" else []
    return {
        "ok": True,
        "status": status,
        "current_balances": {k: _dec_str(v) for k, v in
                             (recon.get("balances") or {}).items()},
        "ledger_derived_balances": {k: _dec_str(v) for k, v in
                                    (recon.get("ledger_totals") or {}).items()},
        "differences": {k: _dec_str(v) for k, v in
                        (recon.get("unexplained_differences") or {}).items()},
        "excluded_internal_transfer_count":
            int(recon.get("internal_transfers") or 0),
        "coverage_start": recon.get("earliest_event"),
        "coverage_end": recon.get("latest_event"),
        "known_limitations": str(recon.get("reconciliation_note") or ""),
        "calculated_at": recon.get("finished_at"),
        "evidence_run_id": recon.get("run_id"),
        "opening_balance_fabricated": False,
        "warnings": warnings,
    }


# ── Denetim (uygulama güvenlik günlüğü) ─────────────────────────────────────

_AUDIT_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z \| (?P<rest>.+)$")
_SENSITIVE_AUDIT = ("password", "secret", "token", "hash", "cookie",
                    "authorization", "api_key")
AUDIT_SEVERITY = {"LOGIN_FAIL": "WARN", "CSRF_FAIL": "WARN",
                  "UNAUTHORIZED_API": "WARN", "APP_LOCKED": "ERROR",
                  "CONFIG_ERROR": "ERROR", "SESSION_EXPIRED": "INFO"}


def _mask_ip(ip: str) -> str:
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:2]) + ".x.x"
    return ip[:8] + "…" if len(ip) > 8 else ip


def _audit_files() -> list[Path]:
    files = [AUDIT_LOG_PATH]
    for i in range(1, 6):
        p = Path(str(AUDIT_LOG_PATH) + f".{i}")
        if p.exists():
            files.append(p)
    return [f for f in files if f.exists()]


def _load_audit_raw() -> dict:
    def build():
        events: list[dict] = []
        files = _audit_files()
        if not files:
            return {"ok": False, "events": [],
                    "error": "AUDIT_UNAVAILABLE"}
        lines: list[str] = []
        for f in files:
            try:
                lines.extend(
                    f.read_text(encoding="utf-8",
                                errors="replace").splitlines())
            except OSError:
                continue
        lines = lines[-AUDIT_MAX_LINES:]
        for idx, line in enumerate(lines):
            m = _AUDIT_LINE.match(line)
            if not m:
                continue
            fields = {"user": "", "ip": "", "detail": "", "event": ""}
            for part in m.group("rest").split(" | "):
                if "=" in part:
                    k, _, v = part.partition("=")
                    if k.strip() in fields:
                        fields[k.strip()] = v.strip()
            ev = fields["event"] or "UNKNOWN"
            detail = fields["detail"]
            low = detail.lower()
            if any(w in low for w in _SENSITIVE_AUDIT):
                detail = "[REDACTED]"
            events.append({
                "audit_id": "AUD-" + hashlib.sha256(
                    f"{idx}|{line}".encode()).hexdigest()[:12],
                "event_type": ev,
                "timestamp": m.group("ts") + "Z",
                "result": "FAIL" if ev in ("LOGIN_FAIL", "CSRF_FAIL",
                                           "UNAUTHORIZED_API", "APP_LOCKED")
                else "OK",
                "source": "security_log",
                "page": (detail.split("page opened: ")[-1]
                         if "page opened:" in detail else ""),
                "client_metadata_masked": _mask_ip(fields["ip"]),
                "message_code": detail[:120],
                "severity": AUDIT_SEVERITY.get(ev, "INFO"),
            })
        return {"ok": True, "events": events, "error": None}
    return _cached("audit_raw", AUDIT_LOG_PATH, build)


AUDIT_SORTS = {"timestamp", "event_type", "severity"}


def parse_audit_filters(args) -> dict:
    limit, offset = parse_page(args, AUDIT_DEFAULT_LIMIT, AUDIT_MAX_LIMIT)
    et = args.get("event_type")
    if et and (len(et) > 32 or not all(c.isalnum() or c == "_" for c in et)):
        raise InvalidParameter("event_type")
    return {
        "event_type": et.upper() if et else None,
        "result": _parse_enum(args.get("result"), {"OK", "FAIL"}, "result"),
        "severity": _parse_enum(args.get("severity"),
                                {"INFO", "WARN", "ERROR"}, "severity"),
        "search": _parse_search(args.get("search")),
        "date_from": _parse_date(args.get("date_from"), "date_from"),
        "date_to": _parse_date(args.get("date_to"), "date_to"),
        "sort": _parse_enum(args.get("sort"), AUDIT_SORTS, "sort",
                            "timestamp"),
        "order": _parse_enum(args.get("order"), {"asc", "desc"}, "order",
                             "desc"),
        "limit": limit, "offset": offset,
    }


def audit_events(f: dict) -> dict:
    raw = _load_audit_raw()
    if raw["error"]:
        return {"ok": False, "events": [],
                "error": {"code": "AUDIT_UNAVAILABLE",
                          "message": "Denetim günlüğü okunamadı."},
                "pagination": {"total": 0, "limit": f["limit"],
                               "offset": f["offset"], "has_more": False},
                "freshness": "KULLANILAMIYOR"}
    rows = raw["events"]
    if f.get("event_type"):
        rows = [r for r in rows if r["event_type"] == f["event_type"]]
    if f.get("result"):
        rows = [r for r in rows if r["result"] == f["result"]]
    if f.get("severity"):
        rows = [r for r in rows if r["severity"] == f["severity"]]
    if f.get("date_from"):
        rows = [r for r in rows if r["timestamp"][:10] >= f["date_from"]]
    if f.get("date_to"):
        rows = [r for r in rows if r["timestamp"][:10] <= f["date_to"]]
    if f.get("search"):
        s = f["search"]
        rows = [r for r in rows if s in r["event_type"].upper()
                or s in r["message_code"].upper()
                or s in r["audit_id"].upper()]
    desc = (f.get("order") or "desc") == "desc"
    rows = sorted(rows, key=lambda r: (r[f.get("sort") or "timestamp"],
                                       r["audit_id"]), reverse=desc)
    total = len(rows)
    page = rows[f["offset"]:f["offset"] + f["limit"]]
    return {"ok": True, "events": page,
            "pagination": {"total": total, "limit": f["limit"],
                           "offset": f["offset"],
                           "has_more": f["offset"] + f["limit"] < total},
            "freshness": "GÜNCEL", "read_only": True}


def audit_summary() -> dict:
    raw = _load_audit_raw()
    if raw["error"]:
        return {"ok": False, "storage_status": "KULLANILAMIYOR",
                "warnings": ["Denetim günlüğü okunamadı."]}
    ev = raw["events"]
    types = [e["event_type"] for e in ev]
    tss = sorted(e["timestamp"] for e in ev)
    return {
        "ok": True,
        "total_event_count": len(ev),
        "login_success_count": types.count("LOGIN_OK"),
        "login_failure_count": types.count("LOGIN_FAIL"),
        "authorization_denial_count": types.count("UNAUTHORIZED_API"),
        "csrf_rejection_count": types.count("CSRF_FAIL"),
        "rate_limit_count": types.count("APP_LOCKED"),
        "refresh_failure_count": 0,
        "export_count": sum(1 for e in ev
                            if "csv export" in e["message_code"].lower()),
        "recent_error_count": sum(1 for e in ev if e["severity"] == "ERROR"),
        "earliest_event": tss[0] if tss else None,
        "latest_event": tss[-1] if tss else None,
        "storage_status": "GÜNCEL",
        "warnings": [],
    }


# ── Rapor kayıt defteri (sabit; kullanıcı yolu yok) ─────────────────────────

_REPORT_REGISTRY: dict[str, dict] = {
    "mission-1200": {"mission_name": "Mission 1200",
                     "path": ROOT / "alpha20_v1" / "mission1200" /
                     "mission_1200_summary.json"},
    "mission-1250": {"mission_name": "Mission 1250",
                     "path": ROOT / "alpha20_v1" / "mission1250" /
                     "mission_1250_summary.json"},
    "mission-1300a": {"mission_name": "Mission 1300A",
                      "path": ROOT / "alpha20_v1" / "mission1300a" /
                      "mission_1300a_report.json"},
    "mission-1300a1": {"mission_name": "Mission 1300A.1",
                       "path": ROOT / "alpha20_v1" / "mission1300a1" /
                       "mission_1300a1_report.json"},
    "mission-1300a2": {"mission_name": "Mission 1300A.2",
                       "path": ROOT / "alpha20_v1" / "mission1300a2" /
                       "mission_1300a2_report.json"},
    "mission-1310a": {"mission_name": "Mission 1310A",
                      "path": ROOT / "alpha20_v1" / "mission1310a" /
                      "mission_1310a_report.json"},
    "mission-1310b": {"mission_name": "Mission 1310B",
                      "path": ROOT / "alpha20_v1" / "mission1310b" /
                      "mission_1310b_report.json"},
    "mission-1400-1": {"mission_name": "Mission 1400.1", "path": None},
    "mission-1400-2": {"mission_name": "Mission 1400.2", "path": None},
    "mission-1400-3": {"mission_name": "Mission 1400.3", "path": None},
    "mission-1400-4": {"mission_name": "Mission 1400.4", "path": None},
}

_REPORT_ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_SENSITIVE_REPORT_KEYS = ("secret", "password", "signature", "credential",
                          "api_key", "apikey", "session", "cookie", "token")


def _sanitize_report(obj: Any) -> Any:
    """Hassas anahtarları ve iç mutlak yolları yinelemeli olarak temizle."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(w in kl for w in _SENSITIVE_REPORT_KEYS) \
                    and not kl.endswith("_masked"):
                continue
            out[k] = _sanitize_report(v)
        return out
    if isinstance(obj, list):
        return [_sanitize_report(x) for x in obj]
    if isinstance(obj, str) and (obj.startswith("/home/")
                                 or obj.startswith("/tmp/")):
        return "[PATH_REDACTED]"
    return obj


def _report_entry(rid: str, meta: dict) -> dict:
    p = meta["path"]
    status = "EKSİK"
    data: dict = {}
    if p is not None and p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
            status = "MEVCUT" if data else "GEÇERSİZ"
        except (OSError, json.JSONDecodeError):
            status = "GEÇERSİZ"
    result = str(data.get("result") or data.get("ledger_reconciliation")
                 or ("PASS" if data else ""))
    return {
        "report_id": rid,
        "mission_name": meta["mission_name"],
        "status": status,
        "result": result or None,
        "run_id": data.get("run_id"),
        "commit": data.get("commit"),
        "timestamp": data.get("finished_at") or data.get("started_at"),
        "total_tests": data.get("tests"),
        "safety_result": ("OK" if str(data.get("order_endpoint_requests",
                                                "0")) == "0" else "İNCELE")
        if data else None,
        "source_type": "JSON" if p is not None else None,
        "downloadable": status == "MEVCUT",
        "available_sections": sorted(data.keys())[:40] if data else [],
    }


def reports_list(limit: int = REPORTS_DEFAULT_LIMIT, offset: int = 0) -> dict:
    rows = [_report_entry(rid, meta)
            for rid, meta in _REPORT_REGISTRY.items()]
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"ok": True, "reports": page,
            "pagination": {"total": total, "limit": limit, "offset": offset,
                           "has_more": offset + limit < total},
            "read_only": True}


def report_detail(rid: str) -> dict | None:
    if not _REPORT_ID_RE.fullmatch(rid or "") or rid not in _REPORT_REGISTRY:
        return None
    meta = _REPORT_REGISTRY[rid]
    entry = _report_entry(rid, meta)
    body: dict = {}
    if entry["status"] == "MEVCUT":
        try:
            body = _sanitize_report(
                json.loads(meta["path"].read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            entry["status"] = "GEÇERSİZ"
    return {"ok": True, "report": entry, "content": body}


def report_download(rid: str) -> tuple[bytes, str] | None:
    d = report_detail(rid)
    if d is None or d["report"]["status"] != "MEVCUT":
        return None
    body = json.dumps(d["content"], indent=2, ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    return body, f"alpha-report-{rid}.json"


# ── CSV dışa aktarım ────────────────────────────────────────────────────────

def ledger_csv(f: dict) -> tuple[bytes, str]:
    integ = ledger_integrity()
    raw = _load_ledger_raw()
    if integ["status"] == "FAIL" or raw["error"]:
        raise RuntimeError("LEDGER_INTEGRITY_FAILED")   # dışa aktarma kapalı
    rows = _filter_ledger(raw["events"], f)[:LEDGER_MAX_LIMIT]
    header = ["timestamp_iso", "exchange", "original_event_type",
              "normalized_event_type", "asset", "amount", "fee",
              "net_amount", "reconciliation_state", "duplicate_status",
              "internal_transfer", "source_transaction_id_masked",
              "raw_payload_hash", "event_id"]
    body_rows = [[
        _csv_text(e["timestamp_iso"]), _csv_text(e["exchange"]),
        _csv_text(e["original_event_type"]),
        _csv_text(e["normalized_event_type"]), _csv_text(e["asset"]),
        _csv_num(e["amount"]), _csv_num(e["fee"]), _csv_num(e["net_amount"]),
        _csv_text(e["reconciliation_state"]),
        _csv_text(e["duplicate_status"]),
        "true" if e["internal_transfer"] else "false",
        _csv_text(e["source_transaction_id_masked"]),
        _csv_text(e["raw_payload_hash"]), _csv_text(e["event_id"]),
    ] for e in rows]
    return (_csv_response_body(header, body_rows),
            f"alpha-ledger-{_stamp()}.csv")


def audit_csv(f: dict) -> tuple[bytes, str]:
    raw = _load_audit_raw()
    if raw["error"]:
        raise RuntimeError("AUDIT_UNAVAILABLE")
    data = audit_events({**f, "limit": AUDIT_MAX_LIMIT, "offset": 0})
    header = ["timestamp", "event_type", "result", "severity", "source",
              "page", "client_metadata_masked", "message_code", "audit_id"]
    body_rows = [[
        _csv_text(e["timestamp"]), _csv_text(e["event_type"]),
        _csv_text(e["result"]), _csv_text(e["severity"]),
        _csv_text(e["source"]), _csv_text(e["page"]),
        _csv_text(e["client_metadata_masked"]),
        _csv_text(e["message_code"]), _csv_text(e["audit_id"]),
    ] for e in data["events"]]
    return (_csv_response_body(header, body_rows),
            f"alpha-audit-{_stamp()}.csv")
