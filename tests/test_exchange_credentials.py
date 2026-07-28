"""Kanonik exchange credential resolver + Windows yerel depo testleri.

Mission sözleşmesi:
- TEK resolver: exchange_credentials.credentials()
- Kanonik isimler legacy alias'lara karşı HER ZAMAN kazanır.
- Windows: yerel depo → kanonik env → legacy env; Replit: yalnız env.
- Depo: atomic, symlink reddi, bozuk JSON'da fail-closed, sır sızmaz.
"""
import json
import os

import pytest

import exchange_credentials as xc
import local_env

ALL_NAMES = ("BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
             "BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET",
             "BINANCE_API_KEY", "BINANCE_API_SECRET",
             "BINANCE_API_Key", "BINANCE_Secret_Key",
             "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ALL_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield


def _patch_store(monkeypatch, tmp_path):
    """Yerel (Windows benzeri) ortam + izole depo dizini."""
    monkeypatch.setattr(local_env, "is_replit", lambda: False)
    monkeypatch.setattr(xc, "ROOT", tmp_path)
    monkeypatch.setattr(xc, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(xc, "FILE", tmp_path / "data" /
                        "exchange_credentials.json")


class TestEnvResolution:
    def test_not_configured_when_empty(self):
        assert xc.credentials("BINANCE_GLOBAL") == ("", "")
        assert xc.configured("BINANCE_GLOBAL") is False
        assert xc.source("BINANCE_GLOBAL") == "NOT_CONFIGURED"

    def test_canonical_global(self, monkeypatch):
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "gk")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "gs")
        assert xc.credentials("BINANCE_GLOBAL") == ("gk", "gs")
        assert xc.source("BINANCE_GLOBAL") == "ENV"

    def test_canonical_beats_every_legacy_alias(self, monkeypatch):
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "canon")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "canonsec")
        monkeypatch.setenv("BINANCE_GLOBAL_API_KEY", "l1")
        monkeypatch.setenv("BINANCE_GLOBAL_API_SECRET", "l1s")
        monkeypatch.setenv("BINANCE_API_KEY", "l2")
        monkeypatch.setenv("BINANCE_API_SECRET", "l2s")
        monkeypatch.setenv("BINANCE_API_Key", "l3")
        monkeypatch.setenv("BINANCE_Secret_Key", "l3s")
        assert xc.credentials("BINANCE_GLOBAL") == ("canon", "canonsec")

    @pytest.mark.parametrize("kname,sname", [
        ("BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET"),
        ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
        ("BINANCE_API_Key", "BINANCE_Secret_Key"),
    ])
    def test_legacy_aliases_still_work(self, monkeypatch, kname, sname):
        monkeypatch.setenv(kname, "lk")
        monkeypatch.setenv(sname, "ls")
        assert xc.credentials("BINANCE_GLOBAL") == ("lk", "ls")

    def test_tr_canonical(self, monkeypatch):
        monkeypatch.setenv("BINANCE_TR_API_KEY", "tk")
        monkeypatch.setenv("BINANCE_TR_API_SECRET", "ts")
        assert xc.credentials("BINANCE_TR") == ("tk", "ts")

    def test_unknown_exchange(self):
        assert xc.credentials("BYBIT") == ("", "")

    def test_replit_ignores_local_store(self, monkeypatch, tmp_path):
        # Replit'te dosya deposu OKUNMAZ; Secrets kanoniktir.
        monkeypatch.setattr(local_env, "is_replit", lambda: True)
        monkeypatch.setattr(xc, "ROOT", tmp_path)
        monkeypatch.setattr(xc, "DATA_DIR", tmp_path / "data")
        f = tmp_path / "data" / "exchange_credentials.json"
        monkeypatch.setattr(xc, "FILE", f)
        f.parent.mkdir()
        f.write_text(json.dumps({"schema_version": 1, "accounts": {
            "BINANCE_GLOBAL": {"api_key": "filek",
                               "api_secret": "files"}}}))
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "envk")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "envs")
        assert xc.credentials("BINANCE_GLOBAL") == ("envk", "envs")


class TestLocalStore:
    def test_save_and_resolve(self, monkeypatch, tmp_path):
        _patch_store(monkeypatch, tmp_path)
        xc.save_local("BINANCE_GLOBAL", "storek", "stores")
        assert xc.credentials("BINANCE_GLOBAL") == ("storek", "stores")
        assert xc.source("BINANCE_GLOBAL") == "LOCAL_STORE"

    def test_store_beats_env(self, monkeypatch, tmp_path):
        _patch_store(monkeypatch, tmp_path)
        xc.save_local("BINANCE_TR", "storek", "stores")
        monkeypatch.setenv("BINANCE_TR_API_KEY", "envk")
        monkeypatch.setenv("BINANCE_TR_API_SECRET", "envs")
        assert xc.credentials("BINANCE_TR") == ("storek", "stores")

    def test_env_fallback_without_store_entry(self, monkeypatch,
                                              tmp_path):
        _patch_store(monkeypatch, tmp_path)
        xc.save_local("BINANCE_TR", "tk", "ts")
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "gk")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "gs")
        assert xc.credentials("BINANCE_GLOBAL") == ("gk", "gs")

    def test_update_preserves_other_exchange(self, monkeypatch,
                                             tmp_path):
        _patch_store(monkeypatch, tmp_path)
        xc.save_local("BINANCE_GLOBAL", "g1", "g2")
        xc.save_local("BINANCE_TR", "t1", "t2")
        xc.save_local("BINANCE_GLOBAL", "g3", "g4")
        assert xc.credentials("BINANCE_GLOBAL") == ("g3", "g4")
        assert xc.credentials("BINANCE_TR") == ("t1", "t2")

    def test_corrupt_file_fail_closed(self, monkeypatch, tmp_path):
        _patch_store(monkeypatch, tmp_path)
        xc.DATA_DIR.mkdir()
        xc.FILE.write_text("{bozuk json!!!")
        assert xc.credentials("BINANCE_GLOBAL") == ("", "")
        # Bozuk dosya + env: env yedeği hâlâ çalışır (dosya kaydı yok).
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "gk")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "gs")
        assert xc.credentials("BINANCE_GLOBAL") == ("gk", "gs")

    def test_symlink_refused(self, monkeypatch, tmp_path):
        _patch_store(monkeypatch, tmp_path)
        xc.DATA_DIR.mkdir()
        target = tmp_path / "evil.json"
        target.write_text("{}")
        xc.FILE.symlink_to(target)
        with pytest.raises(ValueError):
            xc.save_local("BINANCE_GLOBAL", "k", "s")
        assert xc.credentials("BINANCE_GLOBAL") == ("", "")

    def test_save_refused_on_replit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(local_env, "is_replit", lambda: True)
        with pytest.raises(ValueError):
            xc.save_local("BINANCE_GLOBAL", "k", "s")

    @pytest.mark.parametrize("key,sec", [
        ("", "s"), ("k", ""), ("k with space", "s"), ("k", "s\ts"),
        ("x" * 300, "s"), ("k", "y" * 300)])
    def test_validation(self, monkeypatch, tmp_path, key, sec):
        _patch_store(monkeypatch, tmp_path)
        with pytest.raises(ValueError):
            xc.save_local("BINANCE_GLOBAL", key, sec)

    def test_file_permissions(self, monkeypatch, tmp_path):
        _patch_store(monkeypatch, tmp_path)
        xc.save_local("BINANCE_GLOBAL", "k1234", "s1234")
        assert (os.stat(xc.FILE).st_mode & 0o777) == 0o600
        assert (os.stat(xc.DATA_DIR).st_mode & 0o777) == 0o700

    def test_concurrent_saves_preserve_both(self, monkeypatch,
                                            tmp_path):
        # Eşzamanlı yazmalar birbirini ezmez (flock ile RMW korunur).
        import threading
        _patch_store(monkeypatch, tmp_path)
        errs = []

        def w(ex, k, s):
            try:
                for _ in range(5):
                    xc.save_local(ex, k, s)
            except Exception as e:  # pragma: no cover
                errs.append(e)
        t1 = threading.Thread(target=w, args=("BINANCE_GLOBAL",
                                              "gk111", "gs111"))
        t2 = threading.Thread(target=w, args=("BINANCE_TR",
                                              "tk222", "ts222"))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert not errs
        assert xc.credentials("BINANCE_GLOBAL") == ("gk111", "gs111")
        assert xc.credentials("BINANCE_TR") == ("tk222", "ts222")

    def test_separate_from_local_admin(self, monkeypatch, tmp_path):
        # İki güvenlik alanı ayrı dosyalarda yaşar.
        import local_admin
        _patch_store(monkeypatch, tmp_path)
        xc.save_local("BINANCE_GLOBAL", "k1234", "s1234")
        assert xc.FILE.name == "exchange_credentials.json"
        assert local_admin.FILE.name == "local_admin.json"
        assert xc.FILE.name != local_admin.FILE.name


class TestPresence:
    def test_presence_report_no_secrets(self, monkeypatch):
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "SECRETKEY123")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "SECRETSEC123")
        rep = xc.presence_report()
        blob = json.dumps(rep)
        assert "SECRETKEY123" not in blob and "SECRETSEC123" not in blob
        assert rep["BINANCE_GLOBAL"]["key_present"] is True
        assert rep["BINANCE_TR"]["key_present"] is False

    def test_masked_key(self, monkeypatch):
        monkeypatch.setenv("BINANCE_GLOBAL_API_Key", "ABCDEFGH12345678")
        monkeypatch.setenv("BINANCE_GLOBAL_Secret_Key", "s" * 16)
        m = xc.masked_key("BINANCE_GLOBAL")
        assert m.startswith("ABCD") and "*" in m
        assert "EFGH" not in m
