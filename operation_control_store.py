"""Mission 2200 — Operasyon Kontrol paylaşımlı durum deposu.

Gunicorn birden çok worker (süreç) ile çalıştığında otomasyon
durumu, sembol durumları, stop-new-entries bayrağı, idempotency
kayıtları ve denetim zincirinin TÜM worker'larda tutarlı kalmasını
sağlar.

Tasarım:
- Tek JSON anlık görüntü dosyası + ayrı ``flock`` kilit dosyası.
- Her durum değiştiren/okuyan servis çağrısı kilidi alır,
  anlık görüntüyü yükler, işlemi çalıştırır ve (mutasyonlarda)
  sonucu atomik olarak (tmp + fsync + replace) geri yazar.
- ``flock`` süreçler arası, ``threading.RLock`` süreç içi
  eşzamanlılığı serileştirir; kilit yeniden-girişlidir (iç içe
  servis çağrıları kilitlenmez).
- Fail-closed: bozuk anlık görüntü SESSİZCE sıfırlanmaz —
  ``OperationControlStateError`` steril kod ile yükselir; böylece
  daha önce kabul edilmiş bir idempotency anahtarı asla yeniden
  kabul edilemez. Dosya yokluğu temiz kurulumdur (varsayılanlar).
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from operation_control_errors import OperationControlError

__all__ = ["OperationControlStateError",
           "OperationControlStateStore",
           "STATE_SCHEMA_VERSION"]

STATE_SCHEMA_VERSION = 1


class OperationControlStateError(OperationControlError):
    """Paylaşımlı durum deposu hatası — steril kod taşır."""


class OperationControlStateStore:
    """flock + atomik JSON anlık görüntü tabanlı paylaşımlı depo."""

    __slots__ = ("_path", "_lock_path", "_thread_lock",
                 "_depth", "_lock_file")

    def __init__(self, path: os.PathLike | str) -> None:
        if not isinstance(path, (str, os.PathLike)) or not str(path):
            raise OperationControlStateError(
                "STATE_STORE_INVALID:path")
        self._path = Path(path)
        self._lock_path = Path(f"{self._path}.lock")
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._lock_file = None

    @property
    def path(self) -> Path:
        return self._path

    # ── Kilit ────────────────────────────────────────────────

    @contextmanager
    def locked(self):
        """Süreçler-arası + süreç-içi münhasır, yeniden-girişli kilit."""
        with self._thread_lock:
            if self._depth == 0:
                try:
                    self._lock_path.parent.mkdir(
                        parents=True, exist_ok=True)
                    self._lock_file = self._lock_path.open("a+")
                    fcntl.flock(self._lock_file.fileno(),
                                fcntl.LOCK_EX)
                except OSError as exc:
                    if self._lock_file is not None:
                        try:
                            self._lock_file.close()
                        finally:
                            self._lock_file = None
                    raise OperationControlStateError(
                        "STATE_STORE_UNAVAILABLE:lock"
                    ) from exc
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
                if self._depth == 0 and \
                        self._lock_file is not None:
                    try:
                        fcntl.flock(self._lock_file.fileno(),
                                    fcntl.LOCK_UN)
                    finally:
                        self._lock_file.close()
                        self._lock_file = None

    @property
    def in_transaction(self) -> bool:
        """Bu iş parçacığı kilidi tutuyor mu (yeniden-giriş kontrolü)."""
        # RLock sahibi olmayan iş parçacığı acquire(False) alamaz.
        if self._thread_lock.acquire(blocking=False):
            try:
                return self._depth > 0
            finally:
                self._thread_lock.release()
        return False

    # ── Anlık görüntü G/Ç ────────────────────────────────────

    def load(self) -> Optional[dict[str, Any]]:
        """Anlık görüntüyü oku.

        - Dosya yok → ``None`` (temiz kurulum, varsayılanlar).
        - Bozuk/okунamaz → fail-closed steril hata; durum ASLA
          sessizce sıfırlanmaz (idempotency güvenliği)."""
        if not self._path.exists():
            return None
        try:
            raw = self._path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise OperationControlStateError(
                "STATE_STORE_CORRUPT:snapshot") from exc
        if not isinstance(payload, dict) or \
                payload.get("schema_version") != \
                STATE_SCHEMA_VERSION:
            raise OperationControlStateError(
                "STATE_STORE_CORRUPT:schema")
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        """Atomik yaz (tmp + fsync + replace)."""
        if not isinstance(payload, dict):
            raise OperationControlStateError(
                "STATE_STORE_INVALID:payload")
        data = dict(payload)
        data["schema_version"] = STATE_SCHEMA_VERSION
        tmp = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False,
                          separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(self._path)
        except OSError as exc:
            raise OperationControlStateError(
                "STATE_STORE_UNAVAILABLE:write") from exc
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
