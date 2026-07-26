"""Mission 1300A.2 — TRADING API AUTHENTICATION CHECK.

Amaç: Canlı emir göndermeden önce ayrı trading anahtarlarının doğru
çalıştığını doğrulamak. YALNIZCA güvenli GET çağrıları; emir/leverage/
margin/transfer uçlarına giden kod yolu yok. Fail closed.

Kullanım: python tools/mission1300a2.py
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
OUT = ROOT / "alpha20_v1" / "mission1300a2"
OUT.mkdir(exist_ok=True)

PROD_BASE = "https://fapi.binance.com"
ALLOWLIST = {
    ("GET", "/fapi/v2/account"),
    ("GET", "/fapi/v2/balance"),
    ("GET", "/fapi/v2/positionRisk"),
    ("GET", "/fapi/v1/positionSide/dual"),
}
REQUEST_LOG: list[dict] = []


def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


class ReadOnlyClient:
    """Yalnızca GET; allowlist ağ isteğinden ÖNCE uygulanır."""

    def __init__(self, label: str, key: str, secret: str):
        self.label = label
        self.key = key
        self.secret = secret.encode()
        self.sess = requests.Session()
        self.sess.headers["X-MBX-APIKEY"] = key

    def get(self, path: str, params: dict | None = None):
        if ("GET", path) not in ALLOWLIST:
            raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 10000
        qs = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(self.secret, qs.encode(),
                                       hashlib.sha256).hexdigest()
        r = self.sess.get(PROD_BASE + path, params=params, timeout=15)
        REQUEST_LOG.append({"time": datetime.now(timezone.utc).isoformat(),
                            "api": self.label, "method": "GET", "path": path,
                            "status": r.status_code})
        r.raise_for_status()
        return r.json()


def main() -> int:
    ro_key = os.environ.get("BINANCE_API_KEY", "")
    ro_sec = os.environ.get("BINANCE_API_SECRET", "")
    tr_key = os.environ.get("BINANCE_TRADING_API_KEY", "")
    tr_sec = os.environ.get("BINANCE_TRADING_API_SECRET", "")
    if not all([ro_key, ro_sec, tr_key, tr_sec]):
        print("FAIL CLOSED: gerekli secret'lardan en az biri eksik. "
              "Hiçbir istek gönderilmedi.")
        return 2
    print(f"read-only key: {mask(ro_key)} | trading key: {mask(tr_key)} | "
          f"endpoint: {PROD_BASE}")

    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi — görev iptal.")
        return 2

    run_id = f"M1300A2-{uuid.uuid4().hex[:10]}"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()

    auth: dict[str, dict] = {}
    trading_detail: dict = {}
    for label, key, sec in (("read_only", ro_key, ro_sec),
                            ("trading", tr_key, tr_sec)):
        c = ReadOnlyClient(label, key, sec)
        try:
            acct = c.get("/fapi/v2/account")
            auth[label] = {"authentication": "PASS",
                           "key_masked": mask(key),
                           "canTrade": acct.get("canTrade")}
            print(f"  [PASS] {label} authentication "
                  f"(canTrade={acct.get('canTrade')})")
            if label == "trading":
                bal = c.get("/fapi/v2/balance")
                usdt = next((b for b in bal if b["asset"] == "USDT"), {})
                pos = [p for p in c.get("/fapi/v2/positionRisk")
                       if float(p["positionAmt"]) != 0]
                dual = c.get("/fapi/v1/positionSide/dual")["dualSidePosition"]
                trading_detail = {
                    "position_mode": "HEDGE" if dual else "ONE-WAY",
                    "available_usdt": usdt.get("availableBalance"),
                    "total_wallet_balance": acct.get("totalWalletBalance"),
                    "open_positions": len(pos),
                }
        except requests.HTTPError as exc:
            body = exc.response.text[:200] if exc.response is not None else ""
            auth[label] = {"authentication": "FAIL",
                           "key_masked": mask(key), "error": body}
            print(f"  [FAIL] {label} authentication: {body}")

    non_get = [r for r in REQUEST_LOG if r["method"] != "GET"]
    order_reqs = [r for r in REQUEST_LOG
                  if r["path"].rstrip("/").endswith(("/order",
                                                     "/batchOrders"))]
    ok = (auth.get("read_only", {}).get("authentication") == "PASS"
          and auth.get("trading", {}).get("authentication") == "PASS"
          and not non_get and not order_reqs)

    report = {
        "mission": "1300A.2 — Trading API Authentication Check",
        "run_id": run_id, "commit": commit, "tests": test_line,
        "endpoint": PROD_BASE,
        "auth": auth,
        "trading_permission": ("ENABLED" if auth.get("trading", {})
                               .get("canTrade") else "DISABLED"),
        **trading_detail,
        "request_log": REQUEST_LOG,
        "order_endpoint_requests": len(order_reqs),
        "other_write_requests": len(non_get),
        "secrets_exposed": 0,
        "result": "PASS" if ok else "FAIL",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "mission_1300a2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))

    print("\n══════════ MISSION 1300A.2 RAPORU ══════════")
    for k in ("result", "endpoint", "trading_permission", "position_mode",
              "available_usdt", "total_wallet_balance", "open_positions",
              "order_endpoint_requests", "other_write_requests",
              "secrets_exposed", "run_id", "commit"):
        print(f"{k}: {report.get(k)}")
    for r in REQUEST_LOG:
        print(f"  [{r['api']:9}] GET {r['path']:28} → {r['status']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
