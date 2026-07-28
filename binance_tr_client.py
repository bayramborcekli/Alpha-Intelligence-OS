"""Binance TR salt-okunur Spot adaptörü — TEK KAYNAK.

Resmi güncel doküman:
- Base:            https://www.binance.tr
- Public time:     GET /open/v1/common/time
- Signed account:  GET /open/v1/account/spot
- Header:          X-MBX-APIKEY
- İmza:            HMAC-SHA256(secret, exact_query_string); imza query
                   string'in SONUNA eklenir.
- timestamp zorunlu; recvWindow <= 5000 önerilir.

Güvenlik ilkeleri:
- YALNIZ salt-okunur GET; emir/transfer/çekim/Futures kod yolu YOK.
- `session.trust_env = False` (Windows bağımsız testiyle birebir aynı
  davranış: OS proxy/CA env değişkenleri isteğe karışmaz).
- Varsayılan requests TLS doğrulaması kullanılır; custom CA bundle veya
  Windows CA birleştirmesi YOK; TLS doğrulaması ASLA kapatılmaz.
- API key / secret / imza / istek query string'i hiçbir hata mesajına,
  log'a veya çıktıya yazılmaz. Yalnız HTTP status, endpoint path,
  exchange code ve sanitize edilmiş msg dışarı verilebilir.

Binance TR yanıt modeli: {code, msg, data, timestamp}
- code == 0  => başarı
- code != 0  => güvenli hata; exchange_code + sanitize msg KORUNUR
  (INVALID_EXCHANGE_RESPONSE arkasına gizlenmez).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import requests

TR_BASE = "https://www.binance.tr"
PATH_TIME = "/open/v1/common/time"
PATH_SPOT_ACCOUNT = "/open/v1/account/spot"
RECV_WINDOW = 5000
DEFAULT_TIMEOUT = 10

# Salt-okunur allowlist — ağ isteğinden ÖNCE zorunlu.
READ_ONLY_PATHS = frozenset({PATH_TIME, PATH_SPOT_ACCOUNT})

_MAX_MSG_LEN = 200


def sanitize_message(msg: Any) -> str:
    """Borsa msg alanını güvenli metne indirger: yalnız yazdırılabilir
    karakterler, uzunluk sınırı. Secret sızma yüzeyi bırakmaz."""
    text = str(msg or "")
    clean = "".join(ch for ch in text if ch.isprintable())
    return clean[:_MAX_MSG_LEN]


class BinanceTRError(Exception):
    """Normalize edilmiş güvenli Binance TR hatası.

    Alanlar: http_status, path, exchange_code (borsa 'code'),
    exchange_message (sanitize 'msg'), kind (kaba sınıf).
    Key/secret/imza/query İÇERMEZ."""

    def __init__(self, kind: str, http_status: int | None = None,
                 path: str | None = None,
                 exchange_code: int | str | None = None,
                 exchange_message: str = ""):
        self.kind = kind
        self.http_status = http_status
        self.path = path
        self.exchange_code = exchange_code
        self.exchange_message = sanitize_message(exchange_message)
        super().__init__(
            f"{kind} http={http_status} path={path} "
            f"code={exchange_code} msg={self.exchange_message}")


class BinanceTRClient:
    """Binance TR Open API salt-okunur istemcisi (Spot)."""

    def __init__(self, api_key: str = "", api_secret: str = "",
                 timeout: int = DEFAULT_TIMEOUT,
                 session: requests.Session | None = None):
        self._key = api_key
        self._secret = api_secret
        self._timeout = timeout
        self._session = session or requests.Session()
        # Windows'ta doğrulanmış bağımsız testle eşit davranış:
        # OS/user env (proxy, CA bundle değişkenleri vb.) isteğe karışmasın.
        self._session.trust_env = False

    # ── imza ────────────────────────────────────────────────────────────
    def _sign(self, query_string: str) -> str:
        return hmac.new(self._secret.encode(), query_string.encode(),
                        hashlib.sha256).hexdigest()

    def signed_query(self, timestamp: int,
                     recv_window: int = RECV_WINDOW) -> str:
        """İmzalı query string'i üretir: exact string imzalanır, imza
        SONA eklenir."""
        qs = f"timestamp={timestamp}&recvWindow={recv_window}"
        return f"{qs}&signature={self._sign(qs)}"

    # ── HTTP çekirdeği ──────────────────────────────────────────────────
    def _get(self, path: str, query: str = "",
             signed: bool = False) -> dict:
        if path not in READ_ONLY_PATHS:
            raise RuntimeError(f"GÜVENLİK BLOĞU: allowlist dışı GET {path}")
        url = TR_BASE + path + (f"?{query}" if query else "")
        headers = {"X-MBX-APIKEY": self._key} if signed else {}
        try:
            # verify parametresi BİLEREK verilmez → requests varsayılan
            # TLS doğrulaması geçerlidir.
            r = self._session.get(url, headers=headers,
                                  timeout=self._timeout)
        except requests.Timeout:
            raise BinanceTRError("TIMEOUT", path=path)
        except requests.RequestException:
            raise BinanceTRError("NETWORK", path=path)
        body: Any = None
        try:
            body = r.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise BinanceTRError("INVALID_RESPONSE",
                                 http_status=r.status_code, path=path)
        # HTTP 4xx dahil: JSON gövde varsa code/msg KORUNUR.
        code = body.get("code")
        if code in (0, "0"):
            if r.status_code != 200:
                raise BinanceTRError("HTTP_ERROR",
                                     http_status=r.status_code, path=path)
            return body
        raise BinanceTRError("EXCHANGE_ERROR",
                             http_status=r.status_code, path=path,
                             exchange_code=code,
                             exchange_message=body.get("msg", ""))

    # ── genel API ───────────────────────────────────────────────────────
    def get_server_time(self) -> int:
        """GET /open/v1/common/time → sunucu epoch ms."""
        body = self._get(PATH_TIME)
        ts = body.get("timestamp") or (body.get("data") or {}).get(
            "serverTime") if isinstance(body.get("data"), dict) else \
            body.get("timestamp")
        try:
            return int(ts)
        except (TypeError, ValueError):
            raise BinanceTRError("INVALID_RESPONSE", http_status=200,
                                 path=PATH_TIME)

    def get_spot_account(self) -> dict:
        """GET /open/v1/account/spot (imzalı, salt-okunur).

        timestamp olarak sunucu zamanı kullanılır (clock-skew'e dayanıklı);
        sunucu zamanı alınamazsa yerel saate düşer."""
        if not self._key or not self._secret:
            raise BinanceTRError("NOT_CONFIGURED", path=PATH_SPOT_ACCOUNT)
        try:
            ts = self.get_server_time()
        except BinanceTRError:
            ts = int(time.time() * 1000)
        body = self._get(PATH_SPOT_ACCOUNT, self.signed_query(ts),
                         signed=True)
        return body
