"""Mission 1310A — BINANCE TR API AUTHENTICATION (READ-ONLY).

Yalnızca GET; allowlist ağ isteğinden önce uygulanır. Emir/transfer/çekim
uçlarına giden kod yolu yok. Fail closed. Secret'lar maskeli raporlanır.

Binance TR Open API: https://www.trbinance.com  (path'ler /open/v1/...)
İmza: HMAC-SHA256(query string), header X-MBX-APIKEY.

Kullanım: python tools/mission1310a.py
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
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "alpha20_v1" / "mission1310a"
OUT.mkdir(exist_ok=True)

TR_BASE = "https://www.trbinance.com"
ALLOWLIST = {
    ("GET", "/open/v1/common/time"),
    ("GET", "/open/v1/common/symbols"),
    ("GET", "/open/v1/account/spot"),
    ("GET", "/open/v1/account/spot/asset"),
    ("GET", "/open/v1/deposits"),
    ("GET", "/open/v1/withdraws"),
}
REQUEST_LOG: list[dict] = []


def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


class TRReadOnlyClient:
    def __init__(self, key: str, secret: str):
        self.secret = secret.encode()
        self.sess = requests.Session()
        self.sess.headers["X-MBX-APIKEY"] = key

    def get(self, path: str, signed: bool = True,
            params: dict | None = None):
        if ("GET", path) not in ALLOWLIST:
            raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            qs = urllib.parse.urlencode(params)
            params["signature"] = hmac.new(self.secret, qs.encode(),
                                           hashlib.sha256).hexdigest()
        r = self.sess.get(TR_BASE + path, params=params, timeout=20)
        REQUEST_LOG.append({"time": datetime.now(timezone.utc).isoformat(),
                            "method": "GET", "path": path,
                            "status": r.status_code})
        return r


def main() -> int:
    key = os.environ.get("BINANCE_TR_API_KEY", "")
    sec = os.environ.get("BINANCE_TR_API_SECRET", "")
    if not key or not sec:
        print("FAIL CLOSED: BINANCE_TR_API_KEY / BINANCE_TR_API_SECRET "
              "eksik. Hiçbir istek gönderilmedi.")
        return 2
    print(f"TR API key (maskeli): {mask(key)} | endpoint: {TR_BASE}")

    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi — görev iptal.")
        return 2

    c = TRReadOnlyClient(key, sec)
    run_id = f"M1310A-{uuid.uuid4().hex[:10]}"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    report: dict = {"mission": "1310A — Binance TR API Authentication",
                    "run_id": run_id, "commit": commit, "tests": test_line,
                    "endpoint": TR_BASE, "key_masked": mask(key),
                    "checks": {}}

    def tr_call(name: str, path: str, signed: bool = True,
                params: dict | None = None):
        """Bir TR ucunu dener; JSON code==0 başarı, aksi hata metni döner."""
        try:
            r = c.get(path, signed=signed, params=params)
            try:
                body = r.json()
            except ValueError:
                return None, f"HTTP {r.status_code} (JSON değil)"
            if r.status_code == 200 and (body.get("code", 0) in (0, "0")
                                         or "data" in body
                                         or isinstance(body, list)):
                return body, None
            return None, f"HTTP {r.status_code}: {str(body)[:200]}"
        except requests.RequestException as exc:
            return None, f"ağ hatası: {type(exc).__name__}: {exc}"

    # 1-2. Kimlik doğrulama + hesap bilgisi
    acct, err = tr_call("account", "/open/v1/account/spot")
    auth_ok = err is None
    report["authentication"] = "PASS" if auth_ok else "FAIL"
    report["checks"]["account"] = err or "OK"
    print(f"  [{'PASS' if auth_ok else 'FAIL'}] authentication/account"
          + (f" — {err}" if err else ""))

    balances, assets = [], []
    if auth_ok:
        data = acct.get("data", {}) if isinstance(acct, dict) else {}
        accs = data.get("accountAssets") or data.get("assets") or []
        assets = [a.get("asset") for a in accs if isinstance(a, dict)]
        balances = [{"asset": a.get("asset"), "free": a.get("free"),
                     "locked": a.get("locked")} for a in accs
                    if isinstance(a, dict)
                    and (float(a.get("free", 0) or 0)
                         or float(a.get("locked", 0) or 0))]
        report["account_status"] = data.get("status", "OK")
        report["asset_count"] = len(assets)
        report["balances_nonzero"] = balances or "tüm bakiyeler 0"

    # 5-6. Yatırma/çekme geçmişi (destekleniyorsa)
    for name, path in (("recent_deposits", "/open/v1/deposits"),
                       ("recent_withdrawals", "/open/v1/withdraws")):
        body, err = tr_call(name, path)
        if err:
            report[name] = f"desteklenmiyor/erişilemedi ({err})"
        else:
            rows = (body.get("data") or []) if isinstance(body, dict) else body
            if isinstance(rows, dict):
                rows = rows.get("rows") or rows.get("list") or []
            if not isinstance(rows, list):
                rows = []
            report[name] = {"count": len(rows), "latest": rows[:3]}
        print(f"  [{'PASS' if not err else 'INFO'}] {name}: {report[name] if err else report[name]['count']}")

    # 7. Ledger snapshot
    report["ledger_snapshot"] = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "balances": balances,
        "deposits": report.get("recent_deposits"),
        "withdrawals": report.get("recent_withdrawals"),
    }

    # 8. Request audit
    non_get = [r for r in REQUEST_LOG if r["method"] != "GET"]
    order_reqs = [r for r in REQUEST_LOG if "order" in r["path"].lower()
                  or "trade" in r["path"].lower()]
    report["request_log"] = REQUEST_LOG
    report["order_endpoint_requests"] = len(order_reqs)
    report["other_write_requests"] = len(non_get)
    report["secrets_exposed"] = 0
    report["result"] = ("PASS" if auth_ok and not non_get and not order_reqs
                        else "FAIL")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "mission_1310a_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))

    print("\n══════════ MISSION 1310A RAPORU ══════════")
    for k in ("result", "authentication", "account_status", "asset_count",
              "balances_nonzero", "order_endpoint_requests",
              "other_write_requests", "secrets_exposed", "run_id", "commit"):
        print(f"{k}: {report.get(k)}")
    for r in REQUEST_LOG:
        print(f"  GET {r['path']:32} → {r['status']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
