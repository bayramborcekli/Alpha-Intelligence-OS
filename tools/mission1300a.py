"""Mission 1300A — PRODUCTION READ-ONLY doğrulama.

Gerçek Binance USDⓈ-M Futures hesabına SALT-OKUNUR bağlantı.

Güvenlik sözleşmesi (kodla zorlanır):
- İZİN VERİLEN uçlar yalnızca aşağıdaki ALLOWLIST'tedir; hiçbir emir ucu
  (/fapi/v1/order, /fapi/v1/batchOrders, cancel, leverage, marginType vb.)
  listede yoktur ve çağrılamaz.
- Tek POST/DELETE istisnası: /fapi/v1/listenKey (User Data Stream aç/kapat;
  emir değildir).
- Her istek sayaçla kaydedilir; rapor "orders sent = 0" kanıtını istek
  dökümünden üretir.
- API anahtarı loglarda maskelenir; secret hiçbir yere yazılmaz.
- Eksik secret → fail closed (hiçbir istek atılmadan çıkış).

Kullanım: python tools/mission1300a.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "alpha20_v1" / "mission1300a"
OUT.mkdir(exist_ok=True)

PROD_BASE = "https://fapi.binance.com"

# SALT-OKUNUR allowlist: (METHOD, path). Emir uçları kasıtlı olarak YOK.
ALLOWLIST = {
    ("GET", "/fapi/v1/ping"),
    ("GET", "/fapi/v1/time"),
    ("GET", "/fapi/v1/exchangeInfo"),
    ("GET", "/fapi/v1/ticker/price"),
    ("GET", "/fapi/v2/account"),
    ("GET", "/fapi/v2/balance"),
    ("GET", "/fapi/v2/positionRisk"),
    ("GET", "/fapi/v1/openOrders"),
    ("POST", "/fapi/v1/listenKey"),
    ("DELETE", "/fapi/v1/listenKey"),
}
ORDER_PATH_FRAGMENTS = ("order", "leverage", "marginType", "positionMargin",
                        "countdownCancelAll")

REQUEST_LOG: list[dict] = []


def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


class ReadOnlyClient:
    def __init__(self, key: str, secret: str):
        self.key = key
        self.secret = secret.encode()
        self.sess = requests.Session()
        self.sess.headers["X-MBX-APIKEY"] = key

    def request(self, method: str, path: str, signed: bool = False,
                params: dict | None = None):
        if (method, path) not in ALLOWLIST:
            raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı istek "
                               f"{method} {path}")
        if any(f in path for f in ORDER_PATH_FRAGMENTS):
            raise RuntimeError(f"GÜVENLİK BLOĞU: emir/riskli uç {path}")
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 10000
            qs = urllib.parse.urlencode(params)
            params["signature"] = hmac.new(self.secret, qs.encode(),
                                           hashlib.sha256).hexdigest()
        url = PROD_BASE + path
        r = self.sess.request(method, url, params=params, timeout=15)
        REQUEST_LOG.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "method": method, "path": path, "status": r.status_code,
            "used_weight_1m": r.headers.get("X-MBX-USED-WEIGHT-1M"),
        })
        r.raise_for_status()
        return r.json() if r.text else {}


def main() -> int:
    key = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_API_SECRET", "")
    if not key or not secret:
        print("FAIL CLOSED: BINANCE_API_KEY / BINANCE_API_SECRET eksik. "
              "Hiçbir istek gönderilmedi.")
        return 2
    print(f"API key (maskeli): {mask(key)} | endpoint: {PROD_BASE} (PRODUCTION)")

    c = ReadOnlyClient(key, secret)
    report: dict = {"mission": "1300A — PRODUCTION READ-ONLY",
                    "endpoint": PROD_BASE,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "checks": {}}
    ok = True

    def check(name: str, fn):
        nonlocal ok
        try:
            val = fn()
            report["checks"][name] = {"status": "PASS", "detail": val}
            print(f"  [PASS] {name}: {val}")
        except Exception as exc:
            ok = False
            report["checks"][name] = {"status": "FAIL", "detail": str(exc)}
            print(f"  [FAIL] {name}: {exc}")

    # 1-2. Bağlantı + server time
    check("ping", lambda: c.request("GET", "/fapi/v1/ping") == {})
    def server_time():
        st = c.request("GET", "/fapi/v1/time")["serverTime"]
        drift = abs(st - int(time.time() * 1000))
        return {"serverTime": st, "local_drift_ms": drift}
    check("server_time", server_time)

    # 3. Account
    def account():
        a = c.request("GET", "/fapi/v2/account", signed=True)
        return {"canTrade": a.get("canTrade"),
                "totalWalletBalance": a.get("totalWalletBalance"),
                "totalUnrealizedProfit": a.get("totalUnrealizedProfit"),
                "positionSide_hedge": None,
                "assets": len(a.get("assets", [])),
                "positions_fields": len(a.get("positions", []))}
    check("futures_account", account)

    # 4. Bakiye
    def balance():
        b = c.request("GET", "/fapi/v2/balance", signed=True)
        nz = [{"asset": x["asset"], "balance": x["balance"],
               "available": x["availableBalance"]}
              for x in b if float(x["balance"]) != 0]
        return nz or "tüm bakiyeler 0"
    check("futures_balance", balance)

    # 5. Açık pozisyonlar
    def positions():
        p = c.request("GET", "/fapi/v2/positionRisk", signed=True)
        open_p = [{"symbol": x["symbol"], "side": x.get("positionSide"),
                   "amt": x["positionAmt"], "entry": x["entryPrice"],
                   "uPnL": x["unRealizedProfit"]}
                  for x in p if float(x["positionAmt"]) != 0]
        return {"open_position_count": len(open_p), "positions": open_p}
    check("open_positions", positions)

    # 6. Açık emirler
    def open_orders():
        o = c.request("GET", "/fapi/v1/openOrders", signed=True)
        return {"open_order_count": len(o),
                "orders": [{"symbol": x["symbol"], "side": x["side"],
                            "type": x["type"], "qty": x["origQty"]}
                           for x in o]}
    check("open_orders", open_orders)

    # 7. BTCUSDT canlı fiyat
    check("btcusdt_price", lambda: c.request(
        "GET", "/fapi/v1/ticker/price", params={"symbol": "BTCUSDT"}))

    # 8. User Data Stream aç/kapat
    def user_stream():
        lk = c.request("POST", "/fapi/v1/listenKey")["listenKey"]
        c.request("DELETE", "/fapi/v1/listenKey")
        return {"listenKey_opened": True, "listenKey_masked": mask(lk),
                "closed": True}
    check("user_data_stream", user_stream)

    # 9-10. Kanıt: production endpoint + orders sent = 0
    order_requests = [r for r in REQUEST_LOG
                      if any(f in r["path"] for f in ORDER_PATH_FRAGMENTS)]
    write_requests = [r for r in REQUEST_LOG
                      if r["method"] in ("POST", "PUT", "DELETE")
                      and r["path"] != "/fapi/v1/listenKey"]
    report["request_log"] = REQUEST_LOG
    report["orders_sent"] = len(order_requests)
    report["non_listenkey_writes"] = len(write_requests)
    report["total_requests"] = len(REQUEST_LOG)
    report["result"] = ("PASS" if ok and not order_requests
                        and not write_requests else "FAIL")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "mission_1300a_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))

    print("\n══════════ MISSION 1300A RAPORU ══════════")
    print(f"ENDPOINT: {PROD_BASE} (PRODUCTION, salt-okunur allowlist)")
    print(f"TOPLAM İSTEK: {len(REQUEST_LOG)}")
    for r in REQUEST_LOG:
        print(f"  {r['method']:6} {r['path']:28} → {r['status']} "
              f"(weight1m={r['used_weight_1m']})")
    print(f"ORDERS SENT: {len(order_requests)}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
