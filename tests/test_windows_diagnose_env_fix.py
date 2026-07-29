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


# ---------- paper_auto_present_guidance ----------

def test_guidance_false_value(tmp_path):
    content = SECRET_ENV + "ALPHA_WINDOWS_PAPER_AUTO=false\n"
    p = _env(tmp_path, content)
    msg = wd.paper_auto_present_guidance(p)
    assert msg is not None
    assert "'false'" in msg
    assert "ALPHA_WINDOWS_PAPER_AUTO=true" in msg
    # Dosya degismez
    assert p.read_text(encoding="utf-8") == content
    assert not (tmp_path / ".env.bak").exists()


def test_guidance_empty_value(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=\n")
    msg = wd.paper_auto_present_guidance(p)
    assert msg is not None and "(bos)" in msg


def test_guidance_typo_value(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=ture\n")
    msg = wd.paper_auto_present_guidance(p)
    assert msg is not None and "'ture'" in msg


def test_guidance_none_when_true(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=true\n")
    assert wd.paper_auto_present_guidance(p) is None


def test_guidance_none_when_true_mixed_case(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=TRUE\n")
    assert wd.paper_auto_present_guidance(p) is None


def test_guidance_none_when_missing(tmp_path):
    assert wd.paper_auto_present_guidance(_env(tmp_path)) is None
    assert wd.paper_auto_present_guidance(tmp_path / "yok.env") is None


# ---------- paper_auto_env_conflict_guidance ----------

def test_conflict_env_false_dotenv_true(tmp_path):
    content = SECRET_ENV + "ALPHA_WINDOWS_PAPER_AUTO=true\n"
    p = _env(tmp_path, content)
    msg = wd.paper_auto_env_conflict_guidance(p, "false")
    assert msg is not None
    assert "'false'" in msg
    assert "ORTAM DEGISKENINDEN" in msg
    assert "KALDIRIN" in msg
    # Dosya degismez
    assert p.read_text(encoding="utf-8") == content
    assert not (tmp_path / ".env.bak").exists()


def test_conflict_env_empty_dotenv_true(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=true\n")
    msg = wd.paper_auto_env_conflict_guidance(p, "")
    assert msg is not None and "(bos)" in msg


def test_conflict_none_when_env_true(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=true\n")
    assert wd.paper_auto_env_conflict_guidance(p, "true") is None
    assert wd.paper_auto_env_conflict_guidance(p, " TRUE ") is None


def test_conflict_none_when_dotenv_not_true(tmp_path):
    p = _env(tmp_path, "ALPHA_WINDOWS_PAPER_AUTO=false\n")
    assert wd.paper_auto_env_conflict_guidance(p, "false") is None


def test_conflict_none_when_line_missing(tmp_path):
    assert wd.paper_auto_env_conflict_guidance(_env(tmp_path), "false") is None
    assert wd.paper_auto_env_conflict_guidance(tmp_path / "yok.env", "false") is None


# ---------- main() uçtan uca: ENV FAIL + .env çelişkisi ----------

def test_main_prints_conflict_guidance_end_to_end(tmp_path, monkeypatch, capsys):
    """os.environ'da false, .env'de true → main() ONARIM satirini basar.

    Agsiz: requests/socket/subprocess sahte; gercek .env'e dokunulmaz
    (wd.__file__ tmp_path'e yonlendirilir)."""
    import types

    # Sahte .env: deger true → celiski kaynagi ortam degiskeni
    _env(tmp_path, SECRET_ENV + "ALPHA_WINDOWS_PAPER_AUTO=true\n")
    monkeypatch.setattr(wd, "__file__", str(tmp_path / "windows_diagnose.py"))

    # Islem ortaminda false → ENV FAIL dali
    monkeypatch.setenv("ALPHA_WINDOWS_PAPER_AUTO", "false")

    # Ag/subprocess tamamen kapali
    monkeypatch.setattr(wd.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    monkeypatch.setattr(wd.socket, "gethostbyname",
                        lambda host: (_ for _ in ()).throw(OSError("no dns")))

    class _FakeSSLError(Exception):
        pass

    fake_requests = types.ModuleType("requests")
    fake_requests.exceptions = types.SimpleNamespace(SSLError=_FakeSSLError)
    fake_requests.get = lambda *a, **k: (_ for _ in ()).throw(
        ConnectionError("network disabled in test"))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    original = (tmp_path / ".env").read_text(encoding="utf-8")
    rc = wd.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "ONARIM" in out
    assert "ORTAM DEGISKENINDEN" in out
    assert "'false'" in out
    assert "ENV PAPER_AUTO : FAIL" in out
    # Dosya degismez, yedek olusmaz
    assert (tmp_path / ".env").read_text(encoding="utf-8") == original
    assert not (tmp_path / ".env.bak").exists()


def test_main_no_conflict_when_env_true(tmp_path, monkeypatch, capsys):
    """Ortam degiskeni true → ENV PASS, ONARIM satiri yok."""
    import types

    _env(tmp_path, SECRET_ENV + "ALPHA_WINDOWS_PAPER_AUTO=true\n")
    monkeypatch.setattr(wd, "__file__", str(tmp_path / "windows_diagnose.py"))
    monkeypatch.setenv("ALPHA_WINDOWS_PAPER_AUTO", "true")

    monkeypatch.setattr(wd.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    monkeypatch.setattr(wd.socket, "gethostbyname",
                        lambda host: (_ for _ in ()).throw(OSError("no dns")))

    fake_requests = types.ModuleType("requests")
    fake_requests.exceptions = types.SimpleNamespace(SSLError=Exception)
    fake_requests.get = lambda *a, **k: (_ for _ in ()).throw(
        ConnectionError("network disabled in test"))
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    wd.main()
    out = capsys.readouterr().out
    assert "ONARIM" not in out
    assert "ENV PAPER_AUTO : PASS" in out


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

def test_repair_notice_added_mentions_seamless_continue():
    msg = wd.repair_notice("added")
    assert "ENV ONARIMI" in msg
    assert "ALPHA_WINDOWS_PAPER_AUTO=true" in msg
    assert "baslatiliyor" in msg
def test_offer_no_tty(tmp_path):
    def eof(_):
        raise EOFError

    p = _env(tmp_path)
    assert wd.offer_paper_auto_fix(p, ask=eof) == "no_tty"
    assert p.read_text(encoding="utf-8") == SECRET_ENV

def test_repair_notice_silent_for_other_outcomes():
    for outcome in ("", "declined", "no_tty", "present", "already_present"):
        assert wd.repair_notice(outcome) == ""
