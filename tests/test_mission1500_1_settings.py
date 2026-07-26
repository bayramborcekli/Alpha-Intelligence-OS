"""Mission 1500.1 / Agent 09 — Intelligence ayarları testleri."""

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import auth
import intelligence_settings as iset

PASSWORD = "intel-set-parola-1"
HASH = generate_password_hash(PASSWORD)

ALL_ENV = list(iset.SETTING_NAMES) + [iset.ENV_ENABLED_LEGACY]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def client(clean_env, monkeypatch):
    for k in ("ADMIN_PASSWORD_HASH", "ADMIN_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALPHA_OWNER_USERNAME", "sahip")
    monkeypatch.setenv("ALPHA_OWNER_PASSWORD_HASH", HASH)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LOGIN_ATTEMPTS_DB", "/tmp/test_m15001s_attempts.db")
    auth._ATTEMPTS.clear()
    flask_app.app.config["TESTING"] = False
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    try:
        with flask_app.app.test_client() as c:
            yield c
    finally:
        flask_app.app.config["TESTING"] = True


def _login(c):
    return c.post("/api/v1/auth/login",
                  json={"username": "sahip", "password": PASSWORD})


class TestDefaults:
    def test_1500_1_defaults(self, clean_env):
        s = iset.get_settings()
        assert s["enabled"] is False              # güvenli taraf: kapalı
        assert s["local_only"] is True
        assert s["external_llm_enabled"] is False
        assert s["explainability_level"] == "detailed"
        assert s["recommendation_level"] == "advisory"
        assert s["validation_warnings"] == []

    def test_enabled_configurable(self, clean_env):
        clean_env.setenv(iset.ENV_ENABLED, "true")
        assert iset.get_settings()["enabled"] is True
        clean_env.setenv(iset.ENV_ENABLED, "false")
        assert iset.get_settings()["enabled"] is False

    def test_legacy_flag_still_honored(self, clean_env):
        clean_env.setenv(iset.ENV_ENABLED_LEGACY, "true")
        assert iset.get_settings()["enabled"] is True
        # Yeni ad tanımlıysa eskiye baskındır
        clean_env.setenv(iset.ENV_ENABLED, "false")
        assert iset.get_settings()["enabled"] is False


class TestInvalidValues:
    @pytest.mark.parametrize("bad", ["1", "yes", "TRUEISH", "evet", "  "])
    def test_invalid_bool_uses_safe_default(self, clean_env, bad):
        clean_env.setenv(iset.ENV_ENABLED, bad)
        clean_env.setenv(iset.ENV_LOCAL_ONLY, bad)
        s = iset.get_settings()
        assert s["enabled"] is False              # varsayılan
        assert s["local_only"] is True            # varsayılan
        if bad.strip():
            codes = [w["code"] for w in s["validation_warnings"]]
            assert "INVALID_BOOL" in codes

    def test_invalid_choice_uses_safe_default(self, clean_env):
        clean_env.setenv(iset.ENV_EXPLAINABILITY, "verbose")
        clean_env.setenv(iset.ENV_RECOMMENDATION, "aggressive")
        s = iset.get_settings()
        assert s["explainability_level"] == "detailed"
        assert s["recommendation_level"] == "advisory"
        assert sum(1 for w in s["validation_warnings"]
                   if w["code"] == "INVALID_CHOICE") == 2

    def test_warnings_never_contain_raw_values(self, clean_env):
        clean_env.setenv(iset.ENV_EXPLAINABILITY, "HAM_DEGER_XYZ")
        s = iset.get_settings()
        assert "HAM_DEGER_XYZ" not in str(s)

    def test_valid_basic_level(self, clean_env):
        clean_env.setenv(iset.ENV_EXPLAINABILITY, "basic")
        assert iset.get_settings()["explainability_level"] == "basic"


class TestLocalOnlyAndLLM:
    def test_local_only_blocks_external_llm(self, clean_env):
        clean_env.setenv(iset.ENV_EXTERNAL_LLM, "true")
        s = iset.get_settings()
        assert s["external_llm_enabled"] is False
        codes = [w["code"] for w in s["validation_warnings"]]
        assert ("EXTERNAL_LLM_LOCKED" in codes or
                "LOCAL_ONLY_ENFORCED" in codes)

    def test_external_llm_locked_even_without_local_only(self, clean_env):
        # 1500.1 kilidi: local_only=false olsa bile harici LLM kapalı
        clean_env.setenv(iset.ENV_LOCAL_ONLY, "false")
        clean_env.setenv(iset.ENV_EXTERNAL_LLM, "true")
        s = iset.get_settings()
        assert s["external_llm_enabled"] is False
        assert any(w["code"] == "EXTERNAL_LLM_LOCKED"
                   for w in s["validation_warnings"])

    def test_external_llm_default_off(self, clean_env):
        assert iset.get_settings()["external_llm_enabled"] is False


class TestRoutes:
    def test_feature_disabled_safe_response(self, client):
        _login(client)
        r = client.get("/api/intelligence/summary")
        body = r.get_json()
        assert r.status_code == 200
        assert body["enabled"] is False
        assert body["status"] == "UNAVAILABLE"

    def test_new_env_name_enables_routes(self, client, monkeypatch):
        monkeypatch.setenv(iset.ENV_ENABLED, "true")
        _login(client)
        r = client.get("/api/intelligence/status")
        assert r.status_code == 200 and r.get_json()["enabled"] is True

    def test_settings_endpoint(self, client):
        _login(client)
        r = client.get("/api/intelligence/settings")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "no-store, private"
        s = r.get_json()["settings"]
        assert s["local_only"] is True
        assert s["external_llm_enabled"] is False
        assert s["recommendation_level"] == "advisory"

    def test_settings_requires_auth(self, client):
        r = client.get("/api/intelligence/settings")
        assert r.status_code == 401

    def test_settings_read_only_methods(self, client):
        _login(client)
        for method in ("post", "put", "patch", "delete"):
            r = getattr(client, method)("/api/intelligence/settings")
            assert r.status_code == 405, method

    def test_no_raw_env_or_secret_in_response(self, client, monkeypatch):
        monkeypatch.setenv(iset.ENV_EXPLAINABILITY, "GIZLI_HAM_DEGER")
        _login(client)
        blob = client.get("/api/intelligence/settings")\
            .get_data(as_text=True)
        for banned in ("GIZLI_HAM_DEGER", "BINANCE", "API_KEY",
                       "API_SECRET", "PASSWORD_HASH", "SESSION_SECRET",
                       "Traceback"):
            assert banned not in blob, banned
