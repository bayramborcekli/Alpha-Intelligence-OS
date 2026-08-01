#!/usr/bin/env python3
r"""Windows Paper uygulaması için salt-okunur ADR-019 E2E kontrolü.

Yalnız yerel Alpha HTTP GET uçlarını okur. Borsa çağrısı, emir, POST veya
dosya yazımı yoktur. Strateji performansını başarılı ilan etmez; yalnız
Trading Home kartı ile güvenlik sözleşmesinin uygulamada hazır olduğunu
doğrular.

Kullanım (Windows proje kökünde):
    .venv\Scripts\python.exe tools\windows\verify_paper_app.py
"""
from __future__ import annotations

import argparse
import json
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, build_opener


REQUIRED_HOME_MARKERS = (
    "RECOVERY_FOCUSED ADAYI — PAPER DOĞRULAMA",
    "RECOVERY_FOCUSED_V1",
    'id="th-val-status"',
    'id="th-val-hour"',
    'id="th-val-net"',
    'id="th-val-pf"',
    'id="th-val-capacity"',
    'id="th-val-learning"',
)
REQUIRED_PROFILES = {"ADR016_REGIME_NET_EV"}
REQUIRED_STRATEGY_VERSION = "RECOVERY_FOCUSED_V1"


def validate_validation(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["VALIDATION_PAYLOAD_INVALID"]
    findings: list[str] = []
    hourly = data.get("hourly_frequency") or {}
    performance = data.get("performance") or {}
    promotion = data.get("promotion") or {}
    learning = data.get("learning") or {}
    profiles = data.get("included_profiles")
    if data.get("ok") is not True:
        findings.append("VALIDATION_NOT_OK")
    if data.get("live_orders") != "DISABLED":
        findings.append("LIVE_ORDERS_NOT_DISABLED")
    if data.get("exchange_write_requests") != 0:
        findings.append("EXCHANGE_WRITE_REQUESTS_NONZERO")
    if data.get("maximum_open_positions") != 10:
        findings.append("POSITION_LIMIT_NOT_TEN")
    if hourly.get("required_per_full_hour") != 5:
        findings.append("HOURLY_TARGET_NOT_FIVE")
    if hourly.get("force_filled_trades") != 0:
        findings.append("FORCE_FILLED_TRADES_NONZERO")
    if performance.get("minimum_completed_trades_required") != 20:
        findings.append("MINIMUM_TRADE_EVIDENCE_NOT_TWENTY")
    if not isinstance(profiles, list) or set(profiles) != REQUIRED_PROFILES:
        findings.append("QUALIFIED_PROFILES_INVALID")
    if data.get("required_strategy_version") != REQUIRED_STRATEGY_VERSION:
        findings.append("STRATEGY_VERSION_INVALID")
    if data.get("legacy_evidence_excluded") is not True:
        findings.append("LEGACY_EVIDENCE_NOT_EXCLUDED")
    if promotion.get("live_promotion_allowed") is not False:
        findings.append("LIVE_PROMOTION_NOT_LOCKED")
    if learning.get("automatic_code_rewrite_allowed") is not False:
        findings.append("AUTOMATIC_CODE_REWRITE_NOT_LOCKED")
    if learning.get("automatic_live_promotion_allowed") is not False:
        findings.append("AUTOMATIC_LIVE_PROMOTION_NOT_LOCKED")
    if learning.get("structural_strategy_revision_supported") is not False:
        findings.append("STRUCTURAL_REVISION_SCOPE_INVALID")
    if learning.get("status") not in {
            "SCHEDULER_STOPPED", "COLLECTING_EVIDENCE",
            "DIAGNOSIS_ONLY", "CHALLENGER_EVALUATION",
            "PROMOTION_REVIEW_ELIGIBLE"}:
        findings.append("LEARNING_STATUS_INVALID")
    if promotion.get("status") not in {
            "PASS", "FAIL", "INSUFFICIENT_DATA", "NOT_EVALUATED",
            "DATA_UNAVAILABLE"}:
        findings.append("PROMOTION_STATUS_INVALID")
    return findings


def validate_home(html: str) -> list[str]:
    if not isinstance(html, str):
        return ["HOME_PAYLOAD_INVALID"]
    return [f"HOME_MARKER_MISSING:{marker}"
            for marker in REQUIRED_HOME_MARKERS if marker not in html]


def _read(opener, url: str) -> tuple[int, str, str]:
    with opener.open(url, timeout=10) as response:
        return (response.status, response.headers.get("Content-Type", ""),
                response.read().decode("utf-8", "replace"))


def run(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    findings: list[str] = []
    try:
        status, content_type, body = _read(
            opener, base + "/api/paper/validation")
    except (HTTPError, URLError, TimeoutError):
        return ["VALIDATION_ENDPOINT_UNREACHABLE"]
    if status != 200:
        findings.append(f"VALIDATION_HTTP_{status}")
    if "application/json" not in content_type:
        findings.append("VALIDATION_NOT_JSON_AUTH_MAY_BE_REQUIRED")
    else:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            findings.append("VALIDATION_JSON_INVALID")
        else:
            findings.extend(validate_validation(payload))
    try:
        home_status, _, home = _read(opener, base + "/home")
    except (HTTPError, URLError, TimeoutError):
        findings.append("TRADING_HOME_UNREACHABLE")
    else:
        if home_status != 200:
            findings.append(f"TRADING_HOME_HTTP_{home_status}")
        findings.extend(validate_home(home))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    args = parser.parse_args(argv)
    findings = run(args.url)
    if findings:
        print("WINDOWS_PAPER_APP: FAIL")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("WINDOWS_PAPER_APP: PASS")
    print("LIVE_ORDERS: DISABLED")
    print("EXCHANGE_WRITE_REQUESTS: 0")
    print("NOT: Bu sonuç strateji karlilik terfisi degildir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
