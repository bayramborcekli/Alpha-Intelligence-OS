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


# Sanitizer'ın sildiği env isimleri. exchange_credentials.CANONICAL +
# LEGACY içindeki TÜM isimleri kapsamak ZORUNDA — guard testi
# (tests/test_local_env_loading.py) bunu doğrular. Yeni bir alias
# eklersen buraya da ekle.
SANITIZED_CRED_ENV_KEYS = (
    "BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET",
    "BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
    "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "BINANCE_API_Key", "BINANCE_Secret_Key",
)


@pytest.fixture(autouse=True)
def _disable_dev_auth_bypass(monkeypatch):
    """Geliştirme auth bypass'ını test tabanından temizle.

    Replit workspace'inde REPLIT_DEV_BYPASS=1 + REPL_ID set olduğundan
    app._security_gate anonim istekleri otomatik login yapıyor ve tüm
    "anonim reddedilmeli" testleri (73 adet) kırmızıya dönüyordu. Bypass
    davranışını bilerek test eden dosyalar (test_replit_dev_bypass.py,
    test_local_dev_bypass.py) flag'i test içinde kendileri set eder;
    burada silmek onları etkilemez.
    """
    monkeypatch.delenv("REPLIT_DEV_BYPASS", raising=False)
    monkeypatch.delenv("LOCAL_DEV_BYPASS", raising=False)
    yield


@pytest.fixture(autouse=True)
def _sanitize_real_creds(monkeypatch):
    # Testler ASLA gerçek creds ile ağa çıkmamalı: ortamdaki gerçek
    # alias secret'ları test tabanından silinir (testler kendi
    # sahte anahtarlarını monkeypatch.setenv ile koyar).
    for key in SANITIZED_CRED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
