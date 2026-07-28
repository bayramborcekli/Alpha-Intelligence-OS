"""Ortak güvenli borsa taşıma katmanı (salt-okunur GET).

Binance TR ve Binance Global Spot adaptörlerinin paylaştığı kurallar:
- `requests.Session(trust_env=False)` — OS/user proxy & CA env değişkenleri
  isteğe karışmaz (Windows'ta doğrulanmış davranış).
- Varsayılan TLS doğrulaması; custom CA bundle YOK; doğrulama ASLA kapatılmaz.
- Yalnız GET; timeout zorunlu; sınırlı yeniden deneme yalnız güvenli
  (idempotent, 5xx/ağ) hatalarda.
- Yanıt yalnız JSON parse için okunur.
- Hata modeli güvenlidir: http_status, endpoint path, exchange_code,
  sanitize msg, retryable, latency_ms. Key/secret/imza/query/full URL
  hiçbir hata mesajına veya log'a yazılmaz.

İmzalama borsa adaptörünün içinde kalır; bu katman yalnız taşımadır.
"""
from __future__ import annotations

import time
from typing import Any

import requests

DEFAULT_TIMEOUT = 10
MAX_RETRIES = 2
BACKOFF_BASE = 0.4
_MAX_MSG_LEN = 200


def sanitize_message(msg: Any) -> str:
    """Borsa msg alanını güvenli metne indirger."""
    text = str(msg or "")
    clean = "".join(ch for ch in text if ch.isprintable())
    return clean[:_MAX_MSG_LEN]


def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # OS proxy/CA env isteğe karışmasın
    return s


class TransportError(Exception):
    """Güvenli taşıma hatası — secret/imza/query içermez."""

    def __init__(self, kind: str, path: str | None = None,
                 http_status: int | None = None,
                 exchange_code: int | str | None = None,
                 exchange_message: str = "",
                 retryable: bool = False,
                 latency_ms: int | None = None):
        self.kind = kind
        self.path = path
        self.http_status = http_status
        self.exchange_code = exchange_code
        self.exchange_message = sanitize_message(exchange_message)
        self.retryable = retryable
        self.latency_ms = latency_ms
        super().__init__(
            f"{kind} http={http_status} path={path} code={exchange_code} "
            f"msg={self.exchange_message}")


def safe_get_json(session: requests.Session, url: str, path: str,
                  headers: dict | None = None,
                  timeout: int = DEFAULT_TIMEOUT,
                  retries: int = MAX_RETRIES) -> tuple[int, Any, int]:
    """GET → (http_status, parsed_json_or_None, latency_ms).

    Yalnız ağ/5xx hatalarında sınırlı yeniden deneme; 4xx asla
    yeniden denenmez (kimlik/oran hataları riskli)."""
    last: TransportError | None = None
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        try:
            r = session.get(url, headers=headers or {}, timeout=timeout)
            latency = int((time.monotonic() - t0) * 1000)
            try:
                body = r.json()
            except ValueError:
                body = None
            if r.status_code >= 500:
                last = TransportError("HTTP_5XX", path=path,
                                      http_status=r.status_code,
                                      retryable=True, latency_ms=latency)
            else:
                return r.status_code, body, latency
        except requests.Timeout:
            last = TransportError("TIMEOUT", path=path, retryable=True)
        except requests.RequestException:
            last = TransportError("NETWORK", path=path, retryable=True)
        if attempt < retries:
            time.sleep(BACKOFF_BASE * (2 ** attempt))
    raise last or TransportError("NETWORK", path=path, retryable=True)
