# -*- coding: utf-8 -*-
"""MISSION — FIX WINDOWS ENV LOADING regresyonu.

Kanıt: Windows'ta load_project_env sonrası GLOBAL=False / TR=False.
Kök neden: .env dosyası Windows editörlerince farklı kodlamayla
kaydedilir — Notepad UTF-8 BOM ekler (ilk anahtar '\\ufeffKEY' olur),
PowerShell Set-Content varsayılanı UTF-16'dır (utf-8 okuma patlar).
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import local_env
import exchange_credentials as xc

PAIRS = ("BINANCE_GLOBAL_API_Key=GK123456\n"
         "BINANCE_GLOBAL_Secret_Key=GS123456\n"
         "BINANCE_TR_API_KEY=TK123456\n"
         "BINANCE_TR_API_SECRET=TS123456\n")


@pytest.fixture
def clean_env(monkeypatch):
    for k in list(os.environ):
        if "BINANCE" in k:
            monkeypatch.delenv(k, raising=False)
    for k in ("REPL_ID", "REPLIT_DEV_DOMAIN", "FLASK_ENV"):
        monkeypatch.delenv(k, raising=False)
    local_env.reset_for_tests()
    yield
    local_env.reset_for_tests()


def _load_with(tmp_path, payload: bytes):
    f = tmp_path / ".env"
    f.write_bytes(payload)
    with patch.object(local_env, "ENV_FILE", f), \
         patch.object(xc, "_store_entry", return_value=None):
        local_env.load_project_env(force=True)
        return xc.configured("BINANCE_GLOBAL"), xc.configured("BINANCE_TR")


class TestWindowsEnvEncodings:
    def test_utf8_bom_notepad(self, tmp_path, clean_env):
        g, t = _load_with(tmp_path, ("\ufeff" + PAIRS).encode("utf-8"))
        assert (g, t) == (True, True), "BOM'lu .env GLOBAL/TR'yi düşürdü"

    def test_utf16_powershell(self, tmp_path, clean_env):
        g, t = _load_with(tmp_path, PAIRS.encode("utf-16"))
        assert (g, t) == (True, True), "UTF-16 .env okunamadı"

    def test_crlf_plain_utf8(self, tmp_path, clean_env):
        g, t = _load_with(tmp_path,
                          PAIRS.replace("\n", "\r\n").encode("utf-8"))
        assert (g, t) == (True, True)

    def test_garbage_file_fails_closed(self, tmp_path, clean_env):
        g, t = _load_with(tmp_path, b"\xff\xfe\x00garbage\xff")
        assert (g, t) == (False, False)  # istisna YOK, sessiz çöküş yok

    def test_missing_file_no_crash(self, tmp_path, clean_env):
        with patch.object(local_env, "ENV_FILE", tmp_path / ".env"), \
             patch.object(xc, "_store_entry", return_value=None):
            local_env.load_project_env(force=True)
            assert xc.configured("BINANCE_GLOBAL") is False


LEGACY_ONLY = ("BINANCE_API_KEY=LEGK1234\n"
               "BINANCE_API_SECRET=LEGS1234\n")


class TestLegacyNameWarning:
    """Task: eski Binance isimleri dolu + kanonik boşsa net Türkçe uyarı."""

    def _load_env(self, tmp_path, payload: str, caplog):
        f = tmp_path / ".env"
        f.write_text(payload, encoding="utf-8")
        with patch.object(local_env, "ENV_FILE", f), \
             caplog.at_level("WARNING", logger="local_env"):
            local_env.load_project_env(force=True)
        return caplog.text

    def test_legacy_only_warns_in_turkish(self, tmp_path, clean_env, caplog):
        text = self._load_env(tmp_path, LEGACY_ONLY, caplog)
        assert "Eski Binance env ismi tespit edildi" in text
        assert "BINANCE_API_KEY" in text
        assert "BINANCE_GLOBAL_API_Key" in text  # kanonik hedef gösterilir
        # Sır değeri ASLA loglanmaz
        assert "LEGK1234" not in text
        assert "LEGS1234" not in text

    def test_canonical_filled_no_warning(self, tmp_path, clean_env, caplog):
        text = self._load_env(tmp_path, PAIRS, caplog)
        assert "Eski Binance env ismi" not in text

    def test_canonical_plus_legacy_no_warning(self, tmp_path, clean_env,
                                              caplog):
        text = self._load_env(tmp_path, PAIRS + LEGACY_ONLY, caplog)
        assert "Eski Binance env ismi" not in text

    def test_empty_env_no_warning(self, tmp_path, clean_env, caplog):
        text = self._load_env(tmp_path, "", caplog)
        assert "Eski Binance env ismi" not in text

    def test_replit_process_env_legacy_warns(self, monkeypatch, clean_env,
                                             caplog):
        monkeypatch.setenv("REPL_ID", "x")
        monkeypatch.setenv("BINANCE_API_KEY", "LEGK1234")
        monkeypatch.setenv("BINANCE_API_SECRET", "LEGS1234")
        with caplog.at_level("WARNING", logger="local_env"):
            local_env.load_project_env(force=True)
        assert "Eski Binance env ismi tespit edildi" in caplog.text
        assert "LEGK1234" not in caplog.text

    def test_warning_lists_only_filled_legacy_names(self, tmp_path,
                                                    clean_env, caplog):
        text = self._load_env(tmp_path, "BINANCE_API_Key=LK1\n", caplog)
        assert "BINANCE_API_Key" in text
        assert "BINANCE_GLOBAL_API_KEY," not in text
