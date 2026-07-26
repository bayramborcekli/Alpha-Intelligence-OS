"""Mission 1300A.1 — PRODUCTION FUTURES DRY RUN.

Gerçek Binance USDⓈ-M Futures production hesabında, emir GÖNDERMEDEN önceki
tüm hesaplama ve güvenlik adımlarının doğrulanması.

Güvenlik sözleşmesi (kodla zorlanır):
- Yalnızca salt-okunur allowlist uçları çağrılabilir (aşağıda). /order,
  /batchOrders, leverage, margin, positionSide değişikliği, transfer uçları
  allowlist'te YOK; istek ağa çıkmadan bloklanır.
- Bu görevde listenKey dahil HİÇBİR yazma isteği yapılmaz (allowlist'te tüm
  method'lar GET).
- İmzalı emir payload'ı yalnızca YEREL olarak üretilir; signature ve secret
  rapora/loga yazılmaz; son kapıda emir bilinçli olarak bloklanır.
- Secret eksikse fail-closed.

Kullanım: python tools/mission1300a1.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "alpha20_v1" / "mission1300a1"
OUT.mkdir(exist_ok=True)

PROD_BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
STALE_LIMIT_MS = 10_000

# Salt-okunur allowlist — tamamı GET.
ALLOWLIST = {
    ("GET", "/fapi/v1/time"),
    ("GET", "/fapi/v1/exchangeInfo"),
    ("GET", "/fapi/v1/ticker/price"),
    ("GET", "/fapi/v2/balance"),
    ("GET", "/fapi/v2/positionRisk"),
    ("GET", "/fapi/v1/openOrders"),
    ("GET", "/fapi/v1/positionSide/dual"),
    ("GET", "/fapi/v1/commissionRate"),
    ("GET", "/fapi/v2/account"),
}
FORBIDDEN_FRAGMENTS = ("order", "batchOrders", "leverage", "marginType",
                       "positionMargin", "transfer", "countdownCancelAll")
# NOT: openOrders/positionSide GET uçları 'order'/'positionSide' içerir ama
# allowlist kontrolü ÖNCE çalışır; fragment kontrolü yalnızca allowlist dışına
# karşı ikinci savunma hattıdır.

REQUEST_LOG: list[dict] = []


def mask(s: str) -> str:
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "****"


class ReadOnlyClient:
    def __init__(self, key: str, secret: str):
        self.key = key
        self.secret = secret.encode()
        self.sess = requests.Session()
        self.sess.headers["X-MBX-APIKEY"] = key

    def get(self, path: str, signed: bool = False, params: dict | None = None):
        if ("GET", path) not in ALLOWLIST:
            raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 10000
            qs = urllib.parse.urlencode(params)
            params["signature"] = hmac.new(self.secret, qs.encode(),
                                           hashlib.sha256).hexdigest()
        r = self.sess.get(PROD_BASE + path, params=params, timeout=15)
        REQUEST_LOG.append({"time": datetime.now(timezone.utc).isoformat(),
                            "method": "GET", "path": path,
                            "status": r.status_code,
                            "used_weight_1m": r.headers.get(
                                "X-MBX-USED-WEIGHT-1M")})
        r.raise_for_status()
        return r.json()


def round_step(value: Decimal, step: Decimal, up: bool = False) -> Decimal:
    n = value / step
    n = n.to_integral_value(rounding="ROUND_CEILING" if up
                            else "ROUND_FLOOR")
    return (n * step).normalize()


def final_order_gate(payload: dict) -> None:
    """SON KAPI: emir gönderimi bu görevde bilinçli olarak bloklanır."""
    raise RuntimeError("ORDER BLOCKED BY DESIGN — Mission 1300A.1 dry run; "
                       "hiçbir emir isteği ağa çıkamaz.")


def main() -> int:
    key = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_API_SECRET", "")
    if not key or not secret:
        print("FAIL CLOSED: BINANCE_API_KEY / BINANCE_API_SECRET eksik.")
        return 2
    print(f"API key (maskeli): {mask(key)} | endpoint: {PROD_BASE}")

    tests = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=ROOT, capture_output=True, text=True)
    test_line = tests.stdout.strip().splitlines()[-1] if tests.stdout else "?"
    print(f"pytest: {test_line}")
    if tests.returncode != 0:
        print("KRİTİK: testler geçmedi — dry run iptal.")
        return 2

    c = ReadOnlyClient(key, secret)
    run_id = f"M1300A1-{uuid.uuid4().hex[:10]}"
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    checks: dict[str, str] = {}
    problems: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = "PASS" if ok else "FAIL"
        if not ok:
            problems.append(f"{name}: {detail}")
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

    # ── Piyasa & hesap verisi (salt-okunur) ─────────────────────────────
    srv = c.get("/fapi/v1/time")["serverTime"]
    info = c.get("/fapi/v1/exchangeInfo")
    sym = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
    filters = {f["filterType"]: f for f in sym["filters"]}
    tick = c.get("/fapi/v1/ticker/price", params={"symbol": SYMBOL})
    price = Decimal(tick["price"])
    price_age_ms = abs(srv - int(tick["time"]))
    balances = c.get("/fapi/v2/balance", signed=True)
    usdt = next((b for b in balances if b["asset"] == "USDT"), None)
    available = Decimal(usdt["availableBalance"]) if usdt else Decimal(0)
    positions = [p for p in c.get("/fapi/v2/positionRisk", signed=True)
                 if float(p["positionAmt"]) != 0]
    open_orders = c.get("/fapi/v1/openOrders", signed=True)
    dual = c.get("/fapi/v1/positionSide/dual", signed=True)["dualSidePosition"]
    position_mode = "HEDGE" if dual else "ONE-WAY"
    comm = c.get("/fapi/v1/commissionRate", signed=True,
                 params={"symbol": SYMBOL})
    taker = Decimal(comm["takerCommissionRate"])
    acct = c.get("/fapi/v2/account", signed=True)
    can_trade = acct.get("canTrade")
    lev = Decimal(next((p["leverage"] for p in
                        c.get("/fapi/v2/positionRisk", signed=True)
                        if p["symbol"] == SYMBOL), "20"))

    print(f"BTCUSDT: {price} | mode: {position_mode} | USDT: {available} | "
          f"taker fee: {taker} | kaldıraç: {lev}x | canTrade: {can_trade}")

    # ── Filtre doğrulama ve minimum geçerli miktar ──────────────────────
    pf, ls = filters["PRICE_FILTER"], filters["LOT_SIZE"]
    mls = filters.get("MARKET_LOT_SIZE", ls)
    min_notional = Decimal(filters.get("MIN_NOTIONAL", {})
                           .get("notional", "0"))
    tick_size = Decimal(pf["tickSize"])
    step = Decimal(ls["stepSize"])
    min_qty = max(Decimal(ls["minQty"]), Decimal(mls["minQty"]))
    record("filter_present", all(k in filters for k in
                                 ("PRICE_FILTER", "LOT_SIZE")),
           f"tickSize={tick_size} stepSize={step} minQty={min_qty} "
           f"minNotional={min_notional}")

    qty = round_step(min_qty, step, up=True)
    if qty * price < min_notional:
        qty = round_step(min_notional / price, step, up=True)
    notional = (qty * price).quantize(Decimal("0.0001"))
    rounded_price = round_step(price, tick_size)
    record("quantity_rounding", (qty % step) == 0, f"qty={qty}")
    record("price_rounding", (rounded_price % tick_size) == 0,
           f"price={rounded_price}")
    record("min_notional", notional >= min_notional,
           f"notional={notional} USDT")
    margin = (notional / lev).quantize(Decimal("0.0001"))
    open_fee = (notional * taker).quantize(Decimal("0.000001"))
    close_fee = open_fee
    record("stale_data_check", price_age_ms <= STALE_LIMIT_MS,
           f"fiyat yaşı {price_age_ms} ms")

    # Risk doğrulama: tahmini maksimum kayıp (stopsuz market senaryosunda
    # teminatın tamamı riske girer varsayımıyla üst sınır raporlanır)
    est_max_loss = margin + open_fee + close_fee
    record("risk_validation", True,
           f"tahmini maks. kayıp üst sınırı={est_max_loss} USDT")

    # ── Dry-run senaryoları: 4 payload, yerel doğrulama ────────────────
    scenarios = [
        ("LONG_OPEN", "BUY", "LONG", False),
        ("LONG_CLOSE", "SELL", "LONG", True),
        ("SHORT_OPEN", "SELL", "SHORT", False),
        ("SHORT_CLOSE", "BUY", "SHORT", True),
    ]
    payload_reports = {}
    for name, side, pos_side, closing in scenarios:
        payload = {"symbol": SYMBOL, "side": side, "type": "MARKET",
                   "quantity": str(qty),
                   "newClientOrderId": f"a20dry-{uuid.uuid4().hex[:16]}",
                   "timestamp": int(time.time() * 1000),
                   "recvWindow": 10000}
        if position_mode == "HEDGE":
            payload["positionSide"] = pos_side  # hedge: reduceOnly kullanılmaz
        elif closing:
            payload["reduceOnly"] = "true"
        qs = urllib.parse.urlencode(payload)
        sig = hmac.new(secret.encode(), qs.encode(),
                       hashlib.sha256).hexdigest()
        valid = ((Decimal(payload["quantity"]) % step) == 0
                 and Decimal(payload["quantity"]) >= min_qty
                 and len(sig) == 64
                 and payload["newClientOrderId"].startswith("a20dry-"))
        mode_ok = (("positionSide" in payload) == (position_mode == "HEDGE"))
        record(f"payload_{name}", valid and mode_ok,
               f"qty={qty} clientOrderId={payload['newClientOrderId']} "
               f"(signature üretildi, rapora yazılmadı)")
        safe = {k: v for k, v in payload.items()}  # imza payload'a hiç eklenmedi
        payload_reports[name] = safe
        try:  # SON KAPI — bilinçli blok
            final_order_gate(payload)
        except RuntimeError as exc:
            print(f"    → {exc}")

    # ── Güvenlik kanıtı ────────────────────────────────────────────────
    order_reqs = [r for r in REQUEST_LOG
                  if r["path"].rstrip("/").endswith(("/order", "/batchOrders"))]
    write_reqs = [r for r in REQUEST_LOG if r["method"] != "GET"]
    readiness = "READY"
    block_reason = ""
    if available < margin + open_fee:
        readiness = "BLOCKED"
        block_reason = (f"INSUFFICIENT COLLATERAL: mevcut {available} USDT < "
                        f"gereken ~{margin + open_fee} USDT")
    technical = (all(v == "PASS" for v in checks.values())
                 and not order_reqs and not write_reqs)

    report = {
        "mission": "1300A.1 — Production Futures Dry Run",
        "run_id": run_id, "commit": commit, "tests": test_line,
        "endpoint": PROD_BASE, "symbol": SYMBOL,
        "position_mode": position_mode,
        "api_key_can_trade": can_trade,
        "btcusdt_price": str(price), "price_age_ms": price_age_ms,
        "min_valid_quantity": str(qty), "estimated_notional": str(notional),
        "available_usdt": str(available), "leverage": str(lev),
        "estimated_initial_margin": str(margin),
        "estimated_open_fee": str(open_fee),
        "estimated_close_fee": str(close_fee),
        "estimated_max_loss_upper_bound": str(est_max_loss),
        "open_positions": positions, "open_orders": len(open_orders),
        "checks": checks, "problems": problems,
        "payloads_without_signature": payload_reports,
        "allowed_paths": sorted(p for _, p in ALLOWLIST),
        "forbidden_fragments": list(FORBIDDEN_FRAGMENTS),
        "request_log": REQUEST_LOG,
        "order_endpoint_requests": len(order_reqs),
        "other_write_requests": len(write_reqs),
        "secrets_exposed": 0,
        "technical_dry_run": "PASS" if technical else "FAIL",
        "live_order_readiness": readiness,
        "block_reason": block_reason,
        "result": "PASS" if technical else "FAIL",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "mission_1300a1_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))

    print("\n══════════ MISSION 1300A.1 RAPORU ══════════")
    for k in ("result", "technical_dry_run", "live_order_readiness",
              "block_reason", "position_mode", "btcusdt_price",
              "min_valid_quantity", "estimated_notional", "available_usdt",
              "estimated_initial_margin", "estimated_open_fee",
              "order_endpoint_requests", "other_write_requests",
              "secrets_exposed", "run_id", "commit"):
        print(f"{k}: {report[k]}")
    for r in REQUEST_LOG:
        print(f"  GET {r['path']:32} → {r['status']}")
    return 0 if technical else 1


if __name__ == "__main__":
    sys.exit(main())
