"""tools/run_full_suite.py — bölme ve summary ayrıştırma korumaları.

Neden: Tam paket tek koşuda summary basmadan sessizce ölebiliyor (OOM);
koşucu summary'siz çıktıyı FAIL saymak ZORUNDA (yanlış yeşil imkânsız).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import run_full_suite as rfs  # noqa: E402


class TestParseSummaryLine:
    def test_plain_passed(self):
        out = "....\n13650 passed, 3 skipped in 150.12s\n"
        assert rfs.parse_summary_line(out) == {"passed": 13650, "skipped": 3}

    def test_failed_and_errors(self):
        out = "= 5 failed, 10 passed, 2 errors in 2.5s =\n"
        counts = rfs.parse_summary_line(out)
        assert counts == {"failed": 5, "passed": 10, "errors": 2}

    def test_single_error_singular(self):
        counts = rfs.parse_summary_line("1 error, 3 passed in 0.5s")
        assert counts["errors"] == 1

    def test_ansi_codes_stripped(self):
        out = "\x1b[32m42 passed\x1b[0m in 1.00s"
        assert rfs.parse_summary_line(out) == {"passed": 42}

    def test_no_tests_ran(self):
        counts = rfs.parse_summary_line("no tests ran in 0.01s")
        assert counts is not None and counts.get("passed", 0) == 0

    def test_last_summary_wins(self):
        out = "3 passed in 1.0s\nmore output\n7 passed, 1 failed in 2.0s\n"
        assert rfs.parse_summary_line(out) == {"passed": 7, "failed": 1}

    def test_silent_death_returns_none(self):
        # Kritik koruma: dot'lar yazılmış ama summary yok => None (FAIL).
        out = "........................\n....................\n"
        assert rfs.parse_summary_line(out) is None

    def test_empty_output_returns_none(self):
        assert rfs.parse_summary_line("") is None

    def test_passed_mention_without_summary_is_not_summary(self):
        # "passed" kelimesi geçen sıradan log satırı summary sanılmamalı.
        assert rfs.parse_summary_line("checking 3 passed items now\n") is None


class TestSplitFiles:
    def test_two_even_shards(self):
        files = [("a.py", 10), ("b.py", 10), ("c.py", 10), ("d.py", 10)]
        chunks = rfs.split_files(files, 2)
        assert chunks == [["a.py", "b.py"], ["c.py", "d.py"]]

    def test_all_files_kept_exactly_once(self):
        files = [(f"f{i}.py", i % 7 + 1) for i in range(50)]
        for shards in (2, 3, 5):
            chunks = rfs.split_files(files, shards)
            flat = [f for c in chunks for f in c]
            assert flat == [f for f, _ in files]
            assert len(chunks) == shards

    def test_more_shards_than_files_never_empty(self):
        chunks = rfs.split_files([("a.py", 1), ("b.py", 1)], 2)
        assert all(chunks)

    def test_single_shard(self):
        files = [("a.py", 5), ("b.py", 5)]
        assert rfs.split_files(files, 1) == [["a.py", "b.py"]]

    def test_empty_input(self):
        assert rfs.split_files([], 2) == []

    def test_skewed_counts_still_contiguous(self):
        files = [("big.py", 100), ("s1.py", 1), ("s2.py", 1), ("s3.py", 1)]
        chunks = rfs.split_files(files, 2)
        assert chunks[0][0] == "big.py"
        assert [f for c in chunks for f in c] == [f for f, _ in files]
