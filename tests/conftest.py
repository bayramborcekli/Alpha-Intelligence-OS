"""Test tabanı varsayılanları.

Spot-only mimari: Futures kaldırıldı; testler /fapi'ye asla çıkmaz.
"""
import os
import tempfile

import pytest
from werkzeug.security import generate_password_hash


DEFAULT_TEST_ADMIN_HASH = generate_password_hash("testpass1234")


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


@pytest.fixture(autouse=True)
def _reset_rate_limit_state(tmp_path, monkeypatch):
    # 429/418 geri çekilme durumu paylaşılan modül durumu + paylaşımlı
    # dosya — testler arası sızıntı olmasın diye her testten önce/sonra
    # sıfırlanır ve dosya geçici dizine yönlendirilir (gerçek
    # rate_limit_state.json dosyasına dokunulmaz).
    try:
        import alpha20
        monkeypatch.setattr(
            alpha20, "RATE_LIMIT_STATE_PATH",
            tmp_path / "rate_limit_state.json", raising=False)
        alpha20.reset_rate_limit_state()
    except Exception:
        pass
    yield
    try:
        import alpha20
        alpha20.reset_rate_limit_state()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_local_admin_store(tmp_path, monkeypatch):
    """Windows yerel yönetici dosyası testler arasında sızmasın.

    Bazı eski rota/güvenlik testleri gerçek ``data/local_admin.json``
    durumuna bağlı kalıyor; kurulum testinin oluşturduğu dosya sonraki
    testlerin /login ↔ /setup beklentisini sıraya bağlı hale getiriyordu.
    Her test kendi geçici yerel yönetici deposunu ve sahte varsayılan
    kimliği görür; kurulum-yok senaryoları env'i test içinde siler.
    """
    try:
        import local_admin
    except Exception:
        yield
        return
    data_dir = tmp_path / "local-admin-data"
    monkeypatch.setattr(local_admin, "ROOT", tmp_path)
    monkeypatch.setattr(local_admin, "DATA_DIR", data_dir)
    monkeypatch.setattr(local_admin, "FILE", data_dir / "local_admin.json")
    monkeypatch.setenv("ADMIN_USERNAME", "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", DEFAULT_TEST_ADMIN_HASH)
    yield
