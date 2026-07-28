"""Binance Global SPOT salt-okunur adaptörü — TEK KAYNAK.

Yalnız Spot; Futures bu adaptörde YOKTUR ve hiçbir `/fapi/*` yolu
çağrılamaz (allowlist ağ isteğinden önce zorunlu).

- Base:            https://api.binance.com
- Public time:     GET /api/v3/time
- Signed account:  GET /api/v3/account   (X-MBX-APIKEY, HMAC-SHA256)
- Public ticker:   GET /api/v3/ticker/price (opsiyonel değerleme)

İmza: HMAC-SHA256(secret, exact_query_string); imza query string'in
SONUNA eklenir. timestamp sunucu zamanıdır; recvWindow=5000.

Taşıma: exchange_transport (Session trust_env=False, varsayılan TLS
doğrulama, güvenli retry). Key/secret/imza/query asla loglanmaz.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from exchange_transport import (TransportError, make_session,
                                safe_get_json, sanitize_message)

GLOBAL_SPOT_BASE = "https://api.binance.com"
PATH_TIME = "/api/v3/time"
PATH_ACCOUNT = "/api/v3/account"
PATH_TICKER = "/api/v3/ticker/price"
RECV_WINDOW = 5000
DEFAULT_TIMEOUT = 10

# Salt-okunur allowlist — /fapi/* ASLA burada yer alamaz.
READ_ONLY_PATHS = frozenset({PATH_TIME, PATH_ACCOUNT, PATH_TICKER})


class BinanceGlobalError(Exception):
    """Normalize edilmiş güvenli Binance Global hatası (secret'sız)."""

    def __init__(self, kind: str, http_status: int | None = None,
                 path: str | None = None,
                 exchange_code: int | str | None = None,
                 exchange_message: str = "",
                 retryable: bool = False):
        self.kind = kind
        self.http_status = http_status
        self.path = path
        self.exchange_code = exchange_code
        self.exchange_message = sanitize_message(exchange_message)
        self.retryable = retryable
        super().__init__(
            f"{kind} http={http_status} path={path} "
            f"code={exchange_code} msg={self.exchange_message}")


class BinanceGlobalClient:
    """Binance Global Spot salt-okunur istemcisi."""

    def __init__(self, api_key: str = "", api_secret: str = "",
                 timeout: int = DEFAULT_TIMEOUT, session=None):
        self._key = api_key
        self._secret = api_secret
        self._timeout = timeout
        self._session = session or make_session()
        self._session.trust_env = False

    def _sign(self, query_string: str) -> str:
        return hmac.new(self._secret.encode(), query_string.encode(),
                        hashlib.sha256).hexdigest()

    def signed_query(self, timestamp: int,
                     recv_window: int = RECV_WINDOW) -> str:
        qs = f"timestamp={timestamp}&recvWindow={recv_window}"
        return f"{qs}&signature={self._sign(qs)}"

    def _get(self, path: str, query: str = "", signed: bool = False) -> Any:
        if path not in READ_ONLY_PATHS:
            raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
        if path.startswith("/fapi"):
            raise RuntimeError("GÜVENLİK BLOĞU: Futures devre dışı")
        url = GLOBAL_SPOT_BASE + path + (f"?{query}" if query else "")
        headers = {"X-MBX-APIKEY": self._key} if signed else {}
        try:
            status, body, _lat = safe_get_json(
                self._session, url, path, headers=headers,
                timeout=self._timeout)
        except TransportError as exc:
            raise BinanceGlobalError(exc.kind, path=path,
                                     http_status=exc.http_status,
                                     retryable=exc.retryable)
        if status == 200:
            if body is None:
                raise BinanceGlobalError("INVALID_RESPONSE",
                                         http_status=200, path=path)
            return body
        # Binance Global hata zarfı: {"code": -xxxx, "msg": "..."}
        if isinstance(body, dict) and "code" in body:
            raise BinanceGlobalError("EXCHANGE_ERROR", http_status=status,
                                     path=path,
                                     exchange_code=body.get("code"),
                                     exchange_message=body.get("msg", ""),
                                     retryable=status in (418, 429))
        raise BinanceGlobalError("HTTP_ERROR", http_status=status,
                                 path=path, retryable=status in (418, 429))

    # ── genel API (salt-okunur) ─────────────────────────────────────────
    def get_server_time(self) -> int:
        body = self._get(PATH_TIME)
        try:
            return int(body.get("serverTime"))
        except (AttributeError, TypeError, ValueError):
            raise BinanceGlobalError("INVALID_RESPONSE", http_status=200,
                                     path=PATH_TIME)

    def get_spot_account(self) -> dict:
        if not self._key or not self._secret:
            raise BinanceGlobalError("NOT_CONFIGURED", path=PATH_ACCOUNT)
        try:
            ts = self.get_server_time()
        except BinanceGlobalError:
            ts = int(time.time() * 1000)
        return self._get(PATH_ACCOUNT, self.signed_query(ts), signed=True)

    def get_ticker_prices(self) -> list:
        body = self._get(PATH_TICKER)
        if not isinstance(body, list):
            raise BinanceGlobalError("INVALID_RESPONSE", http_status=200,
                                     path=PATH_TICKER)
        return body
