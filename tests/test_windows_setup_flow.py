"""SETUP_AND_START_WINDOWS tek akışının güvenlik/onarım testleri (ağsız)."""
import os
import re
from pathlib import Path

import pytest

import windows_setup_flow as wsf

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def env_sandbox(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    monkeypatch.setattr(wsf, "ENV_PATH", env)
    wsf.report.clear()
    return env


def test_env_created_when_missing(env_sandbox, capsys):
    wsf.repair_env()
    text = env_sandbox.read_text()
    for k, v in wsf.MANAGED.items():
        assert f"{k}={v}" in text
    m = re.search(r"SESSION_SECRET=([0-9a-f]{64})", text)
    assert m, "SESSION_SECRET üretilmeli"
    assert m.group(1) not in capsys.readouterr().out, "secret asla basılmaz"
    assert wsf.report["ENV"] == "PASS"


def test_env_repair_dedupes_and_preserves_secrets(env_sandbox, capsys):
    env_sandbox.write_text(
        "FLASK_ENV=production\nFLASK_ENV=development\n"
        "BINANCE_GLOBAL_API_Key=abc123\nSESSION_SECRET=mysecretvalue\n"
        "# yorum satiri\n")
    wsf.repair_env()
    text = env_sandbox.read_text()
    assert text.count("FLASK_ENV=") == 1
    assert "FLASK_ENV=development" in text
    assert "BINANCE_GLOBAL_API_Key=abc123" in text, "mevcut değer korunur"
    assert text.count("SESSION_SECRET=") == 1
    assert "mysecretvalue" in text, "mevcut secret ezilmez"
    assert "# yorum satiri" in text
    assert "ALPHA_WINDOWS_PAPER_AUTO=true" in text
    backups = list(env_sandbox.parent.glob(".env.backup_*"))
    assert backups, "onarımdan önce yedek alınır"
    assert "mysecretvalue" not in capsys.readouterr().out


def test_env_no_change_no_backup(env_sandbox):
    lines = [f"{k}={v}" for k, v in wsf.MANAGED.items()]
    lines.append("SESSION_SECRET=" + "a" * 64)
    env_sandbox.write_text("\n".join(lines) + "\n")
    wsf.repair_env()
    assert not list(env_sandbox.parent.glob(".env.backup_*"))


def test_live_trading_never_enabled():
    src = (ROOT / "windows_setup_flow.py").read_text()
    assert "ALPHA_ENABLE_LIVE_TRADING" in src
    # Akış bu değişkeni yalnız SİLER, asla set etmez.
    assert not re.search(r"environ\[.ALPHA_ENABLE_LIVE_TRADING.\]\s*=", src)


def test_no_ssl_verification_disabled():
    for name in ("windows_setup_flow.py", "windows_setup.ps1",
                 "SETUP_AND_START_WINDOWS.cmd"):
        src = (ROOT / name).read_text()
        assert "verify=False" not in src
        assert "CERT_NONE" not in src


def test_account_rejected_when_trade_permission(monkeypatch, capsys):
    """İşlem/çekim yetkili anahtar KAYDEDİLMEDEN reddedilir."""
    wsf.report.clear()
    answers = iter(["E", "E", "H"])  # bağlan? / global? / tr?
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(wsf, "_mask_echo_input", lambda *_: "x" * 20)
    import dashboard_api as da
    monkeypatch.setattr(da, "_signed_get",
                        lambda *a, **k: {"canTrade": True,
                                         "canWithdraw": False})
    import exchange_credentials as xc
    saved = []
    monkeypatch.setattr(xc, "save_local",
                        lambda *a: saved.append(a))
    wsf.connect_accounts()
    assert wsf.report["BINANCE GLOBAL ACCOUNT"] == "FAIL"
    assert not saved, "yetkili anahtar depoya yazılmaz"
    out = capsys.readouterr().out
    assert "REDDEDILDI" in out
    assert "x" * 20 not in out, "anahtar terminale basılmaz"


def test_account_saved_when_read_only(monkeypatch):
    wsf.report.clear()
    answers = iter(["E", "E", "H"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(wsf, "_mask_echo_input", lambda *_: "k" * 20)
    import dashboard_api as da
    monkeypatch.setattr(da, "_signed_get",
                        lambda *a, **k: {"canTrade": False,
                                         "canWithdraw": False,
                                         "balances": []})
    import exchange_credentials as xc
    saved = []
    monkeypatch.setattr(xc, "save_local",
                        lambda ex, k, s: saved.append(ex))
    wsf.connect_accounts()
    assert wsf.report["BINANCE GLOBAL ACCOUNT"] == "CONNECTED"
    assert saved == ["BINANCE_GLOBAL"]
