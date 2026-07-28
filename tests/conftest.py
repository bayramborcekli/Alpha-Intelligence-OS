"""Test tabanı varsayılanları.

Spot-only mimari: Futures kaldırıldı; testler /fapi'ye asla çıkmaz.
"""
import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_login_attempts_db():
    """Rate-limit deposunu test oturumuna özel geçici SQLite dosyasına yönlendir.

    login_attempts.db kalıcıdır; önceki koşulardan kalan "setup:127.0.0.1"
    kayıtları /setup/hash testlerini rastgele 429 ile düşürüyordu. Her test
    oturumu kendi boş deposuyla başlar (auth.py LOGIN_ATTEMPTS_DB'yi her
    bağlantıda okur, bu yüzden env erken ayarlanması yeterlidir).
    """
    fd, path = tempfile.mkstemp(prefix="login_attempts_test_", suffix=".db")
    os.close(fd)
    old = os.environ.get("LOGIN_ATTEMPTS_DB")
    os.environ["LOGIN_ATTEMPTS_DB"] = path
    yield
    if old is None:
        os.environ.pop("LOGIN_ATTEMPTS_DB", None)
    else:
        os.environ["LOGIN_ATTEMPTS_DB"] = old
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _sanitize_real_creds(monkeypatch):
    # Testler ASLA gerçek Global creds ile ağa çıkmamalı: ortamdaki
    # gerçek alias secret'ları test tabanından silinir (testler kendi
    # sahte anahtarlarını monkeypatch.setenv ile koyar).
    for key in ("BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET",
                "BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
                "BINANCE_API_KEY", "BINANCE_API_SECRET",
                "BINANCE_API_Key", "BINANCE_Secret_Key"):
        monkeypatch.delenv(key, raising=False)
    yield
