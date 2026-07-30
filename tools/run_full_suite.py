#!/usr/bin/env python3
"""Tam test paketini otomatik bölerek (shard) koşan güvenli koşucu.

Neden: Paket ~13.6k+ teste ulaştı ve tek `pytest` koşusu summary satırı
basmadan sessizce ölebiliyor (muhtemel OOM). "FAILED yok" çıktısı yanlış
yeşil kanıt sanılabilir. Bu koşucu:

1. `pytest --collect-only -q` ile dosya başına test sayısını çıkarır,
2. dosya listesini kümülatif test sayısına göre parçalara böler
   (varsayılan: parça başına en çok 8000 test, en az 2 parça),
3. her parçayı ayrı pytest alt sürecinde koşar,
4. her parçanın SON summary satırını ("N passed ... in Xs") arar —
   summary yoksa o parça KOŞU SESSİZCE ÖLDÜ sayılır ve toplam FAIL olur,
5. tüm parçaların sayaçlarını birleştirip tek konsolide summary basar.

Yanlış yeşil imkânsız: summary'siz parça = FAIL; failed/error > 0 = FAIL;
alt süreç rc != 0 (pytest'in "testler failed" rc=1'i dahil) = FAIL.

Kullanım:
    python tools/run_full_suite.py [--shards N] [--max-per-shard M]
                                   [--pytest-arg=-x ...] [tests/dizin]

Windows CI: tools/windows/run_full_suite.cmd aynı betiği çağırır.
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# pytest -q son satırı: "3 passed, 1 skipped, 2 warnings in 1.23s" veya
# "= 5 failed, 10 passed in 2.5s =" (renk kodları temizlendikten sonra).
SUMMARY_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")
SUMMARY_LINE_RE = re.compile(
    r"(\d+\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b.*\bin\s+"
    r"[\d.]+s|no tests ran in\s+[\d.]+s)")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_summary_line(text: str) -> dict | None:
    """Çıktının SON summary satırını bul ve sayaçları döndür.

    Summary satırı yoksa None döner — çağıran bunu 'koşu sessizce öldü'
    olarak FAIL saymak ZORUNDA.
    """
    for line in reversed(text.splitlines()):
        line = ANSI_RE.sub("", line).strip()
        if not SUMMARY_LINE_RE.search(line):
            continue
        counts: dict[str, int] = {}
        for num, key in SUMMARY_COUNT_RE.findall(line):
            key = "errors" if key.startswith("error") else key
            counts[key] = counts.get(key, 0) + int(num)
        if "no tests ran" in line:
            counts.setdefault("passed", 0)
        return counts
    return None


def split_files(file_counts: list[tuple[str, int]], shards: int) -> list[list[str]]:
    """Dosyaları kümülatif test sayısına göre bitişik parçalara böl."""
    total = sum(c for _, c in file_counts)
    if not file_counts or shards <= 1:
        return [[f for f, _ in file_counts]] if file_counts else []
    target = total / shards
    result: list[list[str]] = []
    current: list[str] = []
    acc = 0
    remaining_shards = shards
    for i, (f, c) in enumerate(file_counts):
        current.append(f)
        acc += c
        remaining_files = len(file_counts) - i - 1
        if (acc >= target and remaining_shards > 1
                and remaining_files >= remaining_shards - 1):
            result.append(current)
            current = []
            acc = 0
            remaining_shards -= 1
    if current:
        result.append(current)
    return result


def collect_file_counts(pytest_target: str, python: str) -> list[tuple[str, int]]:
    proc = subprocess.run(
        [python, "-m", "pytest", pytest_target, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    if proc.returncode not in (0, 5):
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
        raise SystemExit(f"HATA: test toplama (collect) başarısız, rc={proc.returncode}")
    counts: dict[str, int] = {}
    order: list[str] = []
    for line in proc.stdout.splitlines():
        if "::" not in line:
            continue
        fname = line.split("::", 1)[0].strip()
        if fname not in counts:
            counts[fname] = 0
            order.append(fname)
        counts[fname] += 1
    # pytest nodeid'leri rootdir'e görelidir; hedef repo dışındaysa
    # (ör. /tmp altı) dosya adını hedefe göre çözümle.
    def _resolve(fname: str) -> str:
        if (REPO_ROOT / fname).exists() or Path(fname).is_absolute():
            return fname
        base = Path(pytest_target)
        base = base if base.is_dir() else base.parent
        cand = base / fname
        return str(cand) if cand.exists() else fname

    return [(_resolve(f), counts[f]) for f in order]


def run_shard(idx: int, total: int, files: list[str], python: str,
              extra_args: list[str]) -> tuple[dict | None, int, str]:
    print(f"\n=== Parça {idx}/{total}: {len(files)} dosya ===", flush=True)
    proc = subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *extra_args, *files],
        capture_output=True, text=True, cwd=REPO_ROOT)
    tail = "\n".join((proc.stdout or "").splitlines()[-30:])
    print(tail, flush=True)
    if proc.stderr:
        sys.stderr.write(proc.stderr[-2000:])
    return parse_summary_line(proc.stdout or ""), proc.returncode, tail


def run_shards_parallel(chunks: list[list[str]], python: str,
                        extra_args: list[str]) -> list[tuple[dict | None, int]]:
    """Parçaları eşzamanlı alt süreçlerde koş (opt-in: --parallel).

    Dikkat: eşzamanlı parçaların toplam bellek kullanımı tek koşuya
    yaklaşabilir; bellek darsa varsayılan (seri) modda kal.
    """
    procs = []
    for chunk in chunks:
        procs.append(subprocess.Popen(
            [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             *extra_args, *chunk],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=REPO_ROOT))
    results = []
    for i, p in enumerate(procs, 1):
        out, err = p.communicate()
        print(f"\n=== Parça {i}/{len(procs)} ===", flush=True)
        print("\n".join((out or "").splitlines()[-15:]), flush=True)
        if err:
            sys.stderr.write(err[-2000:])
        results.append((parse_summary_line(out or ""), p.returncode))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", nargs="?", default="tests/")
    ap.add_argument("--shards", type=int, default=0,
                    help="parça sayısı (0 = --max-per-shard'a göre otomatik)")
    ap.add_argument("--max-per-shard", type=int, default=8000,
                    help="parça başına en fazla test (otomatik modda)")
    ap.add_argument("--pytest-arg", action="append", default=[],
                    help="her parçaya iletilecek ek pytest argümanı")
    ap.add_argument("--parallel", action="store_true",
                    help="parçaları eşzamanlı koş (bellek darsa KULLANMA)")
    args = ap.parse_args(argv)
    python = sys.executable or "python"

    file_counts = collect_file_counts(args.target, python)
    total_tests = sum(c for _, c in file_counts)
    if total_tests == 0:
        print("HATA: hiç test toplanamadı — FAIL")
        return 2
    shards = args.shards or max(2, math.ceil(total_tests / args.max_per_shard))
    shards = min(shards, len(file_counts))
    chunks = split_files(file_counts, shards)
    print(f"Toplam {total_tests} test, {len(file_counts)} dosya -> "
          f"{len(chunks)} parça (parça başına ~{total_tests // len(chunks)} test)")

    combined: dict[str, int] = {}
    hard_fail = False
    if args.parallel:
        shard_results = run_shards_parallel(chunks, python, args.pytest_arg)
    else:
        shard_results = [
            run_shard(i, len(chunks), chunk, python, args.pytest_arg)[:2]
            for i, chunk in enumerate(chunks, 1)]
    for i, (counts, rc) in enumerate(shard_results, 1):
        if counts is None:
            print(f"!!! Parça {i}: SUMMARY SATIRI YOK — koşu sessizce öldü "
                  f"(rc={rc}). Bu parça FAIL sayıldı; yeşil kanıt GEÇERSİZ.")
            hard_fail = True
            continue
        for k, v in counts.items():
            combined[k] = combined.get(k, 0) + v
        if rc != 0:
            hard_fail = True

    print("\n=== KONSOLİDE SONUÇ ===")
    print(", ".join(f"{v} {k}" for k, v in sorted(combined.items())) or "(sayaç yok)")
    failed = combined.get("failed", 0) + combined.get("errors", 0)
    executed = sum(v for k, v in combined.items() if k != "deselected")
    if not hard_fail and executed < total_tests:
        print(f"!!! Koşulan test sayısı ({executed}) toplanan ({total_tests}) "
              f"altında — FAIL")
        hard_fail = True
    if hard_fail or failed:
        print("SONUÇ: FAIL")
        return 1
    print(f"SONUÇ: PASS ({combined.get('passed', 0)} passed / {total_tests} toplanan)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
