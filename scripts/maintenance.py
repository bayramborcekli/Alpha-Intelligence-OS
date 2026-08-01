"""
Alpha-20 Bakım & Temizlik Görevi — ADR-015.

Her çalıştığında:
1. Eski log dosyalarını arşivle (7+ gün).
2. Büyük log dosyalarını döndür (10 MB eşik).
3. Bozuk JSON runtime dosyalarını tespit et ve temizle.
4. Disk kullanım raporu üret.

PAPER mod güvenliği: hiçbir canlı emir, API anahtarı veya
borsa yazma işlemi yapılmaz.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "alpha20_v1"
ARCHIVE_DIR = ROOT / "archive" / "logs"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

MAX_LOG_AGE_DAYS = 7
MAX_LOG_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_ARCHIVED_LOGS = 10

report: dict = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "steps": [],
}


def _log(step: str, detail: str):
    report["steps"].append({"step": step, "detail": detail})


def archive_old_logs():
    """.log dosyaları 7+ gün eskiyse gzip ile arşivle ve sil."""
    now = time.time()
    cutoff = now - (MAX_LOG_AGE_DAYS * 86400)
    archived = 0
    for log_file in ROOT.glob("*.log"):
        try:
            if log_file.stat().st_mtime < cutoff:
                arc_name = ARCHIVE_DIR / f"{log_file.stem}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log.gz"
                with open(log_file, "rb") as f_in:
                    with gzip.open(arc_name, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                log_file.unlink()
                archived += 1
        except OSError as exc:
            _log("archive_error", f"{log_file.name}: {exc}")
    _log("archive_old_logs", f"Arşivlenen log: {archived}")


def rotate_large_logs():
    """10 MB+ log dosyalarını döndür: .1 -> .2, .log -> .1"""
    rotated = 0
    for log_file in ROOT.glob("*.log"):
        try:
            if log_file.stat().st_size > MAX_LOG_SIZE_BYTES:
                # Shift existing backups
                for i in range(MAX_ARCHIVED_LOGS - 1, 0, -1):
                    older = log_file.with_suffix(f".log.{i}")
                    newer = log_file.with_suffix(f".log.{i+1}")
                    if older.exists():
                        older.replace(newer)
                # Rotate current
                log_file.replace(log_file.with_suffix(".log.1"))
                rotated += 1
        except OSError as exc:
            _log("rotate_error", f"{log_file.name}: {exc}")
    _log("rotate_large_logs", f"Döndürülen log: {rotated}")


def clean_corrupt_json():
    """Parse edilemeyen .json dosyalarını .corrupt olarak yeniden adlandır."""
    corrupt = 0
    for json_file in ROOT.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                json.load(f)
        except (json.JSONDecodeError, OSError):
            try:
                corrupt_path = json_file.with_suffix(".corrupt")
                json_file.rename(corrupt_path)
                corrupt += 1
            except OSError as exc:
                _log("corrupt_error", f"{json_file.name}: {exc}")
    _log("clean_corrupt_json", f"Bozuk JSON temizlendi: {corrupt}")


def trim_archive():
    """Arşiv dizini çok büyüdüyse en eski dosyaları sil."""
    try:
        files = sorted(ARCHIVE_DIR.glob("*.gz"), key=lambda p: p.stat().st_mtime)
        removed = 0
        while len(files) > MAX_ARCHIVED_LOGS:
            files.pop(0).unlink()
            removed += 1
        _log("trim_archive", f"Silinen eski arşiv: {removed}")
    except OSError as exc:
        _log("trim_archive_error", str(exc))


def disk_usage_summary():
    """alpha20_v1/ altındaki toplam boyut."""
    total = 0
    try:
        for root, _dirs, files in os.walk(ROOT):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError as exc:
        _log("disk_usage_error", str(exc))
    _log("disk_usage_mb", round(total / (1024 * 1024), 2))


# ── Çalıştır ──
archive_old_logs()
rotate_large_logs()
clean_corrupt_json()
trim_archive()
disk_usage_summary()

report["finished_at"] = datetime.now(timezone.utc).isoformat()

# AutomationOutput contract
output = {"artifact": report}
print(json.dumps(output, ensure_ascii=False))
