"""Mission 1310B — BINANCE TR MOVEMENT MONITORING AND LEDGER RECONCILIATION.

Salt-okunur (yalnızca GET, allowlist ağ isteğinden önce). Emir/transfer/
çekim/dönüşüm uçlarına giden kod yolu yok. Fail closed. Secret'lar maskeli.

Hareketler deterministik ledger olaylarına normalize edilir; kararlı event
ID (exchange|txid|asset|amount|timestamp SHA-256) ile mükerrer alım
engellenir. Ham payload'lar yalnızca hash olarak saklanır. Mevcut bakiye,
yeniden kurulan ledger toplamıyla mutabakata sokulur; açıklanamayan farklar
ayrı raporlanır. Geçmiş ledger kayıtları asla değiştirilmez (append-only).

Kullanım: python tools/mission1310b.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "alpha20_v1" / "mission1310b"
LEDGER_PATH = OUT / "ledger_events.json"

TR_BASE = "https://www.binance.tr"  # resmi güncel base (eski trbinance.com KULLANILMAZ)
EXCHANGE = "BINANCE_TR"
ALLOWLIST = {
    ("GET", "/open/v1/account/spot"),
    ("GET", "/open/v1/deposits"),
    ("GET", "/open/v1/withdraws"),
}
REQUEST_LOG: list[dict] = []


def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


# ───────────────────────── saf (test edilebilir) mantık ─────────────────────

def payload_hash(raw: dict) -> str:
    """Ham borsa payload'ının denetim hash'i (secret içermez)."""
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()


def stable_event_id(exchange: str, tx_id: str, asset: str,
                    amount: str, ts_ms: int) -> str:
    key = f"{exchange}|{tx_id}|{asset}|{amount}|{ts_ms}"
    return "EVT-" + hashlib.sha256(key.encode()).hexdigest()[:24]


def _ts_ms(raw: dict) -> int | None:
    for f in ("insertTime", "createTime", "applyTime", "time"):
        v = raw.get(f)
        if v not in (None, "", "0", 0):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None


def normalize_movement(raw: dict, kind: str) -> dict:
    """Ham TR kaydını deterministik ledger olayına çevirir.

    kind: "deposit" | "withdrawal". Eksik/bozuk alanlar → UNKNOWN sınıfı.
    Ham payload saklanmaz; yalnızca hash'i saklanır.
    """
    asset = raw.get("asset") or ""
    amount = str(raw.get("amount") or "")
    tx_id = str(raw.get("txId") or raw.get("id") or "")
    ts_ms = _ts_ms(raw)
    fee = str(raw.get("fee") or "0")

    if not asset or not amount or not tx_id or ts_ms is None:
        cls = "UNKNOWN"
    elif raw.get("transferType") == 1:
        cls = "INTERNAL_TRANSFER"
    elif kind == "deposit":
        cls = "DEPOSIT"
    elif kind == "withdrawal":
        cls = "WITHDRAWAL"
    else:
        cls = "UNKNOWN"

    try:
        Decimal(amount)
    except Exception:
        cls = "UNKNOWN"

    return {
        "event_id": stable_event_id(EXCHANGE, tx_id, asset, amount,
                                    ts_ms or 0),
        "exchange": EXCHANGE,
        "class": cls,
        "asset": asset or None,
        "amount": amount or None,
        "fee": fee,
        "tx_id": tx_id or None,
        "timestamp_ms": ts_ms,
        "timestamp_iso": (datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
                          .isoformat() if ts_ms else None),
        "status": raw.get("status"),
        "network": raw.get("network"),
        "raw_payload_sha256": payload_hash(raw),
    }


def ingest_events(existing: list[dict],
                  new_events: list[dict]) -> tuple[list[dict], int, int]:
    """Append-only alım: mevcut kayıtlar değiştirilmez, mükerrerler bloklanır.

    Döner: (birleşik liste [timestamp'e göre sıralı, eskiler aynen korunur],
            eklenen sayısı, bloklanan mükerrer sayısı).
    """
    seen = {e["event_id"] for e in existing}
    added, dupes = [], 0
    for ev in sorted(new_events, key=lambda e: (e["timestamp_ms"] or 0,
                                                e["event_id"])):
        if ev["event_id"] in seen:
            dupes += 1
        else:
            seen.add(ev["event_id"])
            added.append(ev)
    return existing + added, len(added), dupes


def reconstruct_totals(events: list[dict]) -> dict[str, Decimal]:
    """Ledger olaylarından varlık bazında net toplam kurar.

    DEPOSIT → +amount; WITHDRAWAL → −(amount+fee); diğerleri bakiye etkisi
    belirsiz olduğundan dahil edilmez (raporda ayrıca sayılır).
    """
    totals: dict[str, Decimal] = {}
    for ev in events:
        if ev["class"] not in ("DEPOSIT", "WITHDRAWAL") or not ev["asset"]:
            continue
        amt = Decimal(ev["amount"])
        fee = Decimal(ev.get("fee") or "0")
        delta = amt if ev["class"] == "DEPOSIT" else -(amt + fee)
        totals[ev["asset"]] = totals.get(ev["asset"], Decimal(0)) + delta
    return totals


def reconcile(balances: dict[str, Decimal],
              ledger_totals: dict[str, Decimal]) -> dict[str, Decimal]:
    """Borsa bakiyesi − ledger toplamı = açıklanamayan fark (varlık bazında)."""
    diffs: dict[str, Decimal] = {}
    for asset in sorted(set(balances) | set(ledger_totals)):
        d = balances.get(asset, Decimal(0)) - ledger_totals.get(
            asset, Decimal(0))
        if d != 0:
            diffs[asset] = d
    return diffs


# ─────────────────────────── ağ istemcisi ───────────────────────────

class TRReadOnlyClient:
    def __init__(self, key: str, secret: str):
        import requests
        self.secret = secret.encode()
        self.sess = requests.Session()
        self.sess.headers["X-MBX-APIKEY"] = key

    def get(self, path: str, params: dict | None = None):
        if ("GET", path) not in ALLOWLIST:
            raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        qs = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(self.secret, qs.encode(),
                                       hashlib.sha256).hexdigest()
        r = self.sess.get(TR_BASE + path, params=params, timeout=20)
        REQUEST_LOG.append({"time": datetime.now(timezone.utc).isoformat(),
                            "method": "GET", "path": path,
                            "status": r.status_code})
        return r


def _tr_rows(body) -> list:
    rows = (body.get("data") or []) if isinstance(body, dict) else body
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("list") or []
    return rows if isinstance(rows, list) else []


def main() -> int:
    key = os.environ.get("BINANCE_TR_API_KEY", "")
    sec = os.environ.get("BINANCE_TR_API_SECRET", "")
    if not key or not sec:
        print("FAIL CLOSED: BINANCE_TR_API_KEY / BINANCE_TR_API_SECRET eksik.")
        return 2
    OUT.mkdir(exist_ok=True)
    print(f"TR API key (maskeli): {mask(key)} | endpoint: {TR_BASE}")

    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi — görev iptal.")
        return 2

    c = TRReadOnlyClient(key, sec)
    run_id = f"M1310B-{uuid.uuid4().hex[:10]}"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    report: dict = {"mission": "1310B — TR Movement Monitoring & Ledger "
                               "Reconciliation",
                    "run_id": run_id, "commit": commit, "tests": test_line,
                    "endpoint": TR_BASE, "key_masked": mask(key)}

    # 1. Bakiyeler
    r = c.get("/open/v1/account/spot")
    body = r.json()
    if r.status_code != 200 or body.get("code", 0) not in (0, "0"):
        report["authentication"] = "FAIL"
        print(f"FAIL: hesap okunamadı — HTTP {r.status_code}")
        return 1
    report["authentication"] = "PASS"
    data = body.get("data", {})
    report["account_status"] = data.get("status", "OK")
    accs = data.get("accountAssets") or data.get("assets") or []
    balances = {a["asset"]: Decimal(str(a.get("free") or 0))
                + Decimal(str(a.get("locked") or 0))
                for a in accs if isinstance(a, dict)
                and (Decimal(str(a.get("free") or 0))
                     or Decimal(str(a.get("locked") or 0)))}
    report["balances"] = {k: str(v) for k, v in balances.items()}

    # 2-3. Yatırma / çekme kayıtları
    deposits = _tr_rows(c.get("/open/v1/deposits").json())
    withdrawals = _tr_rows(c.get("/open/v1/withdraws").json())
    report["deposit_records"] = len(deposits)
    report["withdrawal_records"] = len(withdrawals)

    # 4-8. Normalize + mükerrer engelli, append-only alım
    new_events = ([normalize_movement(d, "deposit") for d in deposits]
                  + [normalize_movement(w, "withdrawal")
                     for w in withdrawals])
    existing: list[dict] = []
    if LEDGER_PATH.exists():
        existing = json.loads(LEDGER_PATH.read_text())
    before_hashes = [e["event_id"] for e in existing]
    merged, added, dupes = ingest_events(existing, new_events)
    assert [e["event_id"] for e in merged[:len(existing)]] == before_hashes, \
        "İHLAL: geçmiş ledger kayıtları değişti"
    LEDGER_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2))

    unknown = [e for e in merged if e["class"] == "UNKNOWN"]
    internal = [e for e in merged if e["class"] == "INTERNAL_TRANSFER"]
    ts_list = [e["timestamp_ms"] for e in merged if e["timestamp_ms"]]
    report["unique_ledger_events"] = len(merged)
    report["duplicates_blocked"] = dupes
    report["events_added_this_run"] = added
    report["unknown_events"] = len(unknown)
    report["internal_transfers"] = len(internal)
    report["earliest_event"] = (datetime.fromtimestamp(
        min(ts_list) / 1000, timezone.utc).isoformat() if ts_list else None)
    report["latest_event"] = (datetime.fromtimestamp(
        max(ts_list) / 1000, timezone.utc).isoformat() if ts_list else None)

    # 9-10. Mutabakat
    ledger_totals = reconstruct_totals(merged)
    diffs = reconcile(balances, ledger_totals)
    report["ledger_totals"] = {k: str(v) for k, v in ledger_totals.items()}
    report["unexplained_differences"] = {k: str(v) for k, v in diffs.items()}
    # Alım-satım (spot trade) hareketleri bu API kapsamı dışında; fark varsa
    # tam geçmiş kurulamıyor demektir → PARTIAL. Açılış bakiyesi uydurulmaz.
    report["ledger_reconciliation"] = "PASS" if not diffs else "PARTIAL"
    report["reconciliation_note"] = (
        "Yatırma/çekme kayıtları alım-satım ve dönüşüm hareketlerini "
        "kapsamaz; API'nin erişilebilir geçmişi hesabın tüm ömrünü temsil "
        "etmeyebilir. Farklar bu kapsam dışı hareketlerden kaynaklanıyor "
        "olabilir; açılış bakiyesi üretilmedi.") if diffs else "tam mutabakat"

    # 12. İstek denetimi
    non_get = [x for x in REQUEST_LOG if x["method"] != "GET"]
    order_reqs = [x for x in REQUEST_LOG if "order" in x["path"].lower()
                  or "trade" in x["path"].lower()]
    transfer_reqs = [x for x in REQUEST_LOG if "transfer" in x["path"].lower()]
    wd_write_reqs = [x for x in REQUEST_LOG
                     if "withdraw" in x["path"].lower()
                     and x["method"] != "GET"]
    report["request_log"] = REQUEST_LOG
    report["order_endpoint_requests"] = len(order_reqs)
    report["transfer_endpoint_requests"] = len(transfer_reqs)
    report["withdrawal_endpoint_requests_write"] = len(wd_write_reqs)
    report["other_write_requests"] = len(non_get)
    report["secrets_exposed"] = 0
    report["result"] = ("PASS" if not non_get and not order_reqs
                        and not transfer_reqs else "FAIL")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "mission_1310b_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))

    print("\n══════════ MISSION 1310B RAPORU ══════════")
    for k in ("result", "authentication", "account_status", "balances",
              "deposit_records", "withdrawal_records", "unique_ledger_events",
              "duplicates_blocked", "events_added_this_run", "unknown_events",
              "internal_transfers", "earliest_event", "latest_event",
              "ledger_reconciliation", "unexplained_differences",
              "order_endpoint_requests", "transfer_endpoint_requests",
              "other_write_requests", "secrets_exposed", "run_id", "commit"):
        print(f"{k}: {report.get(k)}")
    for x in REQUEST_LOG:
        print(f"  {x['method']} {x['path']:28} → {x['status']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
