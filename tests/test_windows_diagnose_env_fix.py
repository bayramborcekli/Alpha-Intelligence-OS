"""Task: teşhis eksik ALPHA_WINDOWS_PAPER_AUTO'yu onayla .env'e eklesin.

Ağsız testler: satır ekleme, mevcut değeri ezmeme, secret satırlarına
dokunmama, yedek alma, onay/ret akışı.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import windows_diagnose as wd  # noqa: E402

SECRET_ENV = (
    "# yorum satiri\n"
    "BINANCE_GLOBAL_API_Key=SECRETKEY123\n"
    "BINANCE_GLOBAL_Secret_Key=SECRETVAL456\n"
    "FLASK_SECRET_KEY=abc\n"
)


def _env(tmp_path: Path, content: str | None = SECRET_ENV) -> Path:
    p = tmp_path / ".env"
    if content is not None:
        p.write_text(content, encoding="utf-8")
    return p


# ---------- paper_auto_status ----------

def test_status_missing_file(tmp_path):
    assert wd.paper_auto_status(tmp_path / ".env") == "missing_file"


def test_status_missing_line(tmp_path):
    assert wd.paper_auto_status(_env(tmp_path)) == "missing_line"


def test_status_present(tmp_path):
    p = _env(tmp_path, SECRET_ENV + "ALPHA_WINDOWS_PAPER_AUTO=true\n")
    assert wd.paper_auto_status(p) == "present"


def test_status_present_even_if_false(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=false\n")
    assert wd.paper_auto_status(p) == "present"


def test_status_ignores_comments(tmp_path):
    p = _env(tmp_path, "# ALPHA_WINDOWS_PAPER_AUTO=true\n")
    assert wd.paper_auto_status(p) == "missing_line"


def test_status_bom_utf8(tmp_path):
    p = tmp_path / ".env"
    p.write_bytes(b"\xef\xbb\xbfALPHA_WINDOWS_PAPER_AUTO=true\n")
    assert wd.paper_auto_status(p) == "present"


# ---------- add_paper_auto_line ----------

def test_add_appends_only_the_line(tmp_path):
    p = _env(tmp_path)
    assert wd.add_paper_auto_line(p) == "added"
    text = p.read_text(encoding="utf-8")
    # Mevcut secret satırları bayt bayt korunur
    assert text.startswith(SECRET_ENV)
    assert text == SECRET_ENV + "ALPHA_WINDOWS_PAPER_AUTO=true\n"


def test_add_does_not_overwrite_existing_value(tmp_path):
    content = SECRET_ENV + "ALPHA_WINDOWS_PAPER_AUTO=false\n"
    p = _env(tmp_path, content)
    assert wd.add_paper_auto_line(p) == "already_present"
    assert p.read_text(encoding="utf-8") == content
    assert not (tmp_path / ".env.bak").exists()


def test_add_creates_backup(tmp_path):
    p = _env(tmp_path)
    wd.add_paper_auto_line(p)
    bak = tmp_path / ".env.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == SECRET_ENV


def test_add_missing_file_creates_it(tmp_path):
    p = tmp_path / ".env"
    assert wd.add_paper_auto_line(p) == "added"
    assert p.read_text(encoding="utf-8") == "ALPHA_WINDOWS_PAPER_AUTO=true\n"


def test_add_handles_no_trailing_newline(tmp_path):
    p = _env(tmp_path, "FLASK_SECRET_KEY=abc")
    wd.add_paper_auto_line(p)
    assert p.read_text(encoding="utf-8") == (
        "FLASK_SECRET_KEY=abc\nALPHA_WINDOWS_PAPER_AUTO=true\n")


def test_add_preserves_crlf(tmp_path):
    p = tmp_path / ".env"
    p.write_bytes(b"FLASK_SECRET_KEY=abc\r\n")
    wd.add_paper_auto_line(p)
    assert p.read_bytes() == (
        b"FLASK_SECRET_KEY=abc\r\nALPHA_WINDOWS_PAPER_AUTO=true\r\n")


# ---------- offer_paper_auto_fix ----------

def test_offer_accept_adds_and_sets_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_WINDOWS_PAPER_AUTO", raising=False)
    p = _env(tmp_path)
    assert wd.offer_paper_auto_fix(p, ask=lambda _: "E") == "added"
    assert "ALPHA_WINDOWS_PAPER_AUTO=true" in p.read_text(encoding="utf-8")
    import os
    assert os.environ.get("ALPHA_WINDOWS_PAPER_AUTO") == "true"


def test_offer_decline_leaves_file_untouched(tmp_path):
    p = _env(tmp_path)
    assert wd.offer_paper_auto_fix(p, ask=lambda _: "H") == "declined"
    assert p.read_text(encoding="utf-8") == SECRET_ENV
    assert not (tmp_path / ".env.bak").exists()


def test_offer_present_short_circuits(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=true\n")

    def boom(_):
        raise AssertionError("var olan anahtar icin sorulmamali")

    assert wd.offer_paper_auto_fix(p, ask=boom) == "present"


def test_offer_no_tty(tmp_path):
    def eof(_):
        raise EOFError

    p = _env(tmp_path)
    assert wd.offer_paper_auto_fix(p, ask=eof) == "no_tty"
    assert p.read_text(encoding="utf-8") == SECRET_ENV
