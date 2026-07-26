"""Intelligence Timeline Engine — Mission 1500.2 / Agent 02.

Append-only (ekle-yalnız) zaman sıralı Intelligence geçmişi.

Kurallar:
- Kayıtlar yalnızca EKLENİR; güncelleme, silme, üzerine yazma ve
  truncate YOKTUR (dosya yalnızca "a" modunda açılır).
- Yalnızca güvenli alan beyaz listesi saklanır; secret/credential/
  exchange/ledger/audit/kullanıcı alanları hem üst düzeyde hem iç içe
  anahtar taramasıyla reddedilir.
- Para değerleri Decimal-string olarak saklanır; float kabul edilmez.
- Bilinmeyen değer null veya "—" olarak korunur; asla 0 üretilmez.
- Deterministik serileştirme: sort_keys + sabit ayraçlar → aynı kayıt
  her zaman aynı JSON satırını üretir.
- Bu modül HTTP, borsa, ledger, audit veya ağ erişimi İÇERMEZ.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# Varsayılan geçmiş dosyası (risk_history.jsonl emsali — kök dizin).
DEFAULT_HISTORY_PATH = Path("intelligence_history.jsonl")

# Tek kayıt için üst sınır (bayt) ve dosya için kayıt tavanı.
MAX_RECORD_BYTES = 16_384
MAX_RECORDS = 5_000

# Saklanmasına izin verilen üst düzey alanlar (beyaz liste).
ALLOWED_FIELDS = (
    "generated_at",
    "status",
    "partial",
    "freshness",
    "insights",
    "recommendations",
    "warnings",
    "portfolio_summary",
    "risk_summary",
    "risk_explanations",
    "advisory_only",
)

# Anahtar adında geçmesi kaydı reddettiren parçalar (küçük harf).
FORBIDDEN_KEY_PARTS = (
    "api_key", "apikey", "secret", "token", "cookie", "session",
    "credential", "password", "passwd", "csrf", "authorization",
    "exchange", "ledger", "audit", "private_key",
    "user", "email",
)


class TimelineError(ValueError):
    """Timeline kural ihlali (güvenli, sterile mesajlarla)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _history_path(path: str | os.PathLike | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("ALPHA_INTELLIGENCE_HISTORY_PATH", "").strip()
    return Path(env) if env else DEFAULT_HISTORY_PATH


def _check_keys(obj: Any, trail: str = "") -> None:
    """İç içe tüm anahtarları yasaklı parçalara karşı tarar."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for part in FORBIDDEN_KEY_PARTS:
                if part in kl:
                    raise TimelineError(
                        "FORBIDDEN_FIELD",
                        "Kayıt, saklanması yasak bir alan içeriyor",
                    )
            _check_keys(v, f"{trail}.{k}")
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _check_keys(item, trail)


def _normalize(value: Any) -> Any:
    """Decimal → string; float REDDEDİLİR; bilinmeyenler aynen korunur."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        raise TimelineError(
            "FLOAT_FORBIDDEN", "Finansal alanlarda float saklanamaz"
        )
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    # Bilinmeyen tip: dürüst davran — uydurma, reddet.
    raise TimelineError("UNSUPPORTED_TYPE", "Serileştirilemeyen alan tipi")


def _canonical_json(record: dict) -> str:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def build_record(snapshot: dict) -> dict:
    """Özet sözlüğünden güvenli, deterministik timeline kaydı üretir.

    Yalnızca beyaz listedeki alanlar alınır; eksik alanlar null olur
    (asla 0 uydurulmaz). advisory_only her kayıtta True'ya sabitlenir.
    """
    if not isinstance(snapshot, dict):
        raise TimelineError("INVALID_SNAPSHOT", "Kayıt sözlük olmalıdır")
    record: dict[str, Any] = {"v": SCHEMA_VERSION}
    for field in ALLOWED_FIELDS:
        record[field] = _normalize(snapshot.get(field, None))
    # Advisory-only politikası kayıt düzeyinde de değiştirilemez.
    record["advisory_only"] = True
    record["read_only"] = True
    _check_keys(record)
    return record


def append_snapshot(snapshot: dict,
                    path: str | os.PathLike | None = None) -> dict:
    """Kaydı ekle-yalnız JSONL dosyasının sonuna ekler ve kaydı döndürür.

    Dosya YALNIZCA append ("a") modunda açılır; mevcut içerik hiçbir
    koşulda değiştirilmez veya kısaltılmaz.
    """
    record = build_record(snapshot)
    line = _canonical_json(record)
    encoded = line.encode("utf-8")
    if len(encoded) > MAX_RECORD_BYTES:
        raise TimelineError("RECORD_TOO_LARGE", "Kayıt boyut sınırını aşıyor")
    p = _history_path(path)
    # Çoklu worker güvenliği: tavan denetimi + ekleme tek kilit altında
    # yapılır; böylece iki süreç aynı anda tavanı aşamaz.
    with p.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            if count(p) >= MAX_RECORDS:
                # Ekle-yalnız kural gereği eski kayıt silinmez; yeni durur.
                raise TimelineError(
                    "HISTORY_FULL", "Geçmiş kayıt tavanına ulaşıldı")
            fh.write(line + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return record


def load_history(path: str | os.PathLike | None = None,
                 limit: int | None = None) -> list[dict]:
    """Tüm geçerli kayıtları dosya sırasıyla (eski → yeni) döndürür.

    Bozuk satırlar atlanır (tolerant okuma); sıralama dosya sırasıdır —
    yeniden sıralama yapılmaz, böylece ekleme sırası korunur.
    """
    p = _history_path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, TypeError):
                continue  # bozuk satır — geçmiş asla "onarılmaz"
            if isinstance(rec, dict):
                out.append(rec)
    if limit is not None and limit >= 0:
        out = out[-limit:] if limit else []
    return out


def get_latest(n: int = 1,
               path: str | os.PathLike | None = None) -> list[dict]:
    """Son N kaydı (eski → yeni sırayla) döndürür; geçmiş boşsa []."""
    if n <= 0:
        return []
    return load_history(path, limit=n)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_by_timerange(start: str | None, end: str | None,
                     path: str | os.PathLike | None = None) -> list[dict]:
    """generated_at değeri [start, end] aralığındaki kayıtları döndürür.

    Sınırlar dahildir; None sınır açık uçtur. generated_at değeri
    çözümlenemeyen kayıtlar dürüstlük gereği DIŞARIDA bırakılır
    (aralığa uyduğu iddia edilmez).
    """
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    out: list[dict] = []
    for rec in load_history(path):
        ts = _parse_iso(rec.get("generated_at"))
        if ts is None:
            continue
        if start_dt is not None and ts < start_dt:
            continue
        if end_dt is not None and ts > end_dt:
            continue
        out.append(rec)
    return out


def count(path: str | os.PathLike | None = None) -> int:
    """Dosyadaki geçerli kayıt sayısı (boş/bozuk satırlar sayılmaz)."""
    return len(load_history(path))
