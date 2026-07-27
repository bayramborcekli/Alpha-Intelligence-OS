"""Mission 2200 Agent 02 — Çalışma alanı API zarfları + CSV (saf).

Agent 01 zarf sözleşmesini (operation_control_api.read_envelope)
aynen kullanır; ek olarak salt-okunur CSV dışa aktarım üretir.

CSV kuralları:
- Yalnız görünüm alanları; sır/ham istisna metni asla girmez.
- Formül enjeksiyonuna karşı hücre başı `=`, `+`, `-`, `@`
  önüne tek tırnak eklenir (spreadsheet güvenliği).
- None → "UNKNOWN"; Decimal → düz metin (bilimsel gösterim yok).
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Optional, Sequence

from operation_control_api import read_envelope, serialize_view
from operation_control_models import OperationSnapshot

__all__ = [
    "workspace_envelope", "serialize_rows", "rows_to_csv",
    "CSV_EXPORTS",
]

# Dışa aktarım adları → zarf veri anahtarı (rota tarafında
# hangi görünüm listesinin dökümü yapılacağını belirler).
CSV_EXPORTS = frozenset({"positions", "orders", "signals",
                         "journal"})


def workspace_envelope(data: object,
                       snapshot: Optional[OperationSnapshot],
                       correlation_id: str,
                       generated_at: int) -> dict:
    """Agent 01 okuma zarfıyla birebir aynı sözleşme.
    read_envelope (payload, 200) döndürür; burada yalnız payload
    döner (HTTP kodu rota tarafında sabit 200'dür)."""
    payload, _ = read_envelope(data, snapshot, correlation_id,
                               generated_at)
    return payload


def serialize_rows(rows: Sequence[object]) -> list:
    """Dataclass görünümlerini veya düz eşlemleri JSON-güvenli
    sözlüklere çevirir (Decimal → metin; sahte dönüşüm yok)."""
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append({key: (format(val, "f")
                              if isinstance(val, Decimal) else val)
                        for key, val in row.items()})
        else:
            out.append(serialize_view(row))
    return out


def _csv_cell(value: object) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    # Formül enjeksiyonu koruması: baştaki boşluk/denetim
    # karakterleri atlanarak tetikleyici aranır (`\t=`, ` =`,
    # `\n=` gibi baypaslar da yakalanır); hücre metin kalır.
    stripped = text.lstrip(" \t\r\n\f\v\x00\x1a")
    if stripped[:1] in ("=", "+", "-", "@"):
        text = "'" + text
    return text


def rows_to_csv(rows: Sequence[object]) -> str:
    """Görünüm listesinden deterministik CSV üret. Boş liste →
    yalnız 'empty' başlığı (dosya asla sahte satır içermez)."""
    serialized = serialize_rows(rows)
    buffer = io.StringIO()
    if not serialized:
        buffer.write("empty\r\n")
        return buffer.getvalue()
    fieldnames = list(serialized[0].keys())
    writer = csv.DictWriter(buffer, fieldnames=fieldnames,
                            extrasaction="ignore")
    writer.writeheader()
    for row in serialized:
        writer.writerow({key: _csv_cell(row.get(key))
                         for key in fieldnames})
    return buffer.getvalue()
