"""Mission — PERMANENT WINDOWS CONFIGURATION & SINGLE SOURCE OF TRUTH.

Ağsız kalıcılık testleri: credential deposu SETUP/.env onarımı/git pull
simülasyonundan etkilenmez; durum dosyası secret içermez ve silinse bile
yeniden oluşturulur; silme yalnız disconnect ile olur.
"""
import json
import os
from pathlib import Path

import pytest

import exchange_credentials as xc
import windows_setup_flow as wsf
from services import binance_connection as bc
from services import secure_credentials as sc

ROOT = Path(__file__).resolve().parent.parent

KEY = "K" * 24
SEC = "S" * 40


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Depo + snapshot + audit + .env dosyalarını tmp'e izole eder."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(xc, "DATA_DIR", data)
    monkeypatch.setattr(xc, "FILE", data / "exchange_credentials.json")
    monkeypatch.setattr(xc, "ROOT", tmp_path)
    monkeypatch.setattr(bc, "DATA_DIR", data)
    monkeypatch.setattr(bc, "SNAPSHOT_PATH", data / "snap.json")
    monkeypatch.setattr(bc, "AUDIT_PATH", data / "audit.jsonl")
    monkeypatch.setattr(bc, "INTEGRATION_STATUS_PATH",
                        data / "integration_status.json")
    monkeypatch.setattr(wsf, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(xc.local_env, "is_replit", lambda: False)
    for k in ("BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
              "BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _store_global():
    sc.store("BINANCE_GLOBAL", KEY, SEC)


def test_facade_is_canonical_roundtrip(iso):
    """secure_credentials tek kanonik servis: yaz/oku/kaynak/sil."""
    _store_global()
    assert sc.configured("BINANCE_GLOBAL")
    assert sc.credentials("BINANCE_GLOBAL") == (KEY, SEC)
    assert sc.source("BINANCE_GLOBAL") == "LOCAL_STORE"
    # nt dışı test ortamında etiket local_file; nt'de windows_dpapi
    assert sc.credential_store("BINANCE_GLOBAL") in (
        sc.STORE_WINDOWS_DPAPI, sc.STORE_LOCAL_FILE)
    assert sc.remove("BINANCE_GLOBAL") is True
    assert not sc.configured("BINANCE_GLOBAL")
    assert sc.credential_store("BINANCE_GLOBAL") == sc.STORE_NONE


def test_env_repair_does_not_touch_credential_store(iso):
    """SETUP .env onarımı credential deposuna DOKUNMAZ + yedek alır."""
    _store_global()
    before = xc.FILE.read_bytes()
    wsf.ENV_PATH.write_text("FLASK_ENV=production\nMY_CUSTOM=keepme\n",
                            encoding="utf-8")
    wsf.repair_env()
    assert xc.FILE.read_bytes() == before  # depo bayt bayt aynı
    text = wsf.ENV_PATH.read_text(encoding="utf-8")
    assert "MY_CUSTOM=keepme" in text          # kullanıcı satırı korunur
    assert "FLASK_ENV=development" in text     # yönetilen değer onarılır
    backups = list(iso.glob(".env.backup_*"))
    assert backups, ".env değişmeden önce timestamp'li yedek alınmalı"
    assert sc.credentials("BINANCE_GLOBAL") == (KEY, SEC)


def test_setup_rerun_and_restart_preserve_credentials(iso):
    """SETUP tekrar koşusu / uygulama restart'ı credential silmez."""
    _store_global()
    # restart simülasyonu: depo dosyasından taze okuma (bellek durumu yok)
    assert xc.configured("BINANCE_GLOBAL")
    # SETUP rerun simülasyonu: repair_env + startup test (ağsız mock)
    wsf.ENV_PATH.write_text("PAPER_MODE=true\n", encoding="utf-8")
    wsf.repair_env()
    called = {}

    def fake_test(provider):
        called[provider] = True
        return {"provider": provider, "status": "CONNECTED_READ_ONLY"}
    orig = bc.test_stored
    bc.test_stored = fake_test
    try:
        outcomes = bc.run_startup_tests()
    finally:
        bc.test_stored = orig
    assert outcomes.get("BINANCE_GLOBAL") == "CONNECTED_READ_ONLY"
    assert called.get("BINANCE_GLOBAL")
    assert sc.configured("BINANCE_GLOBAL")  # hiçbir adım silmedi


def test_git_pull_cannot_delete_store(iso):
    """git pull simülasyonu: depo dosyası gitignore'da → pull etkilemez."""
    _store_global()
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gitignore or "exchange_credentials" in gitignore
    # pull yalnız takip edilen dosyaları değiştirir; depo takip DIŞI
    assert sc.configured("BINANCE_GLOBAL")


def test_integration_status_whitelist_and_no_secret(iso, monkeypatch):
    """Durum dosyası yalnız izinli alanları içerir; secret/tam key yok."""
    _store_global()
    bc._save_snapshot("BINANCE_GLOBAL", {
        "status": "CONNECTED_READ_ONLY", "tested_at": "2026-07-29T00:00:00",
        "account_type": "SPOT", "futures": "NOT_TESTED"})
    raw = bc.INTEGRATION_STATUS_PATH.read_text(encoding="utf-8")
    assert SEC not in raw and KEY not in raw
    data = json.loads(raw)
    for provider, entry in data.items():
        assert set(entry) <= set(bc.INTEGRATION_STATUS_FIELDS)
    g = data["BINANCE_GLOBAL"]
    assert g["connection_status"] == "CONNECTED_READ_ONLY"
    assert g["permission_status"] == "READ_ONLY"
    assert g["futures_status"] == "NOT_TESTED"
    assert g["masked_api_key"].startswith(KEY[:4])
    assert "*" in g["masked_api_key"]
    assert g["credential_store"] in ("windows_dpapi", "local_file")


def test_status_rebuilds_deleted_integration_status(iso):
    """Durum dosyası silinse bile depo kaydından yeniden oluşturulur."""
    _store_global()
    bc._save_snapshot("BINANCE_GLOBAL", {"status": "CONNECTED_READ_ONLY",
                                         "tested_at": "t"})
    bc.INTEGRATION_STATUS_PATH.unlink()
    out = bc.status()
    assert bc.INTEGRATION_STATUS_PATH.exists()
    assert out["BINANCE_GLOBAL"]["status"] == "CONNECTED_READ_ONLY"
    assert out["live_orders"] == "DISABLED"
    assert out["BINANCE_GLOBAL"]["storage"]["git_status"].startswith(
        "Repository dışında")


def test_disconnect_is_only_delete_path(iso):
    """Yalnız disconnect siler; geçici test hatası silmez."""
    _store_global()

    def boom(*a, **k):
        raise OSError("tls reset")
    orig = bc._TESTERS["BINANCE_GLOBAL"]
    bc._TESTERS = dict(bc._TESTERS)
    bc._TESTERS["BINANCE_GLOBAL"] = boom
    try:
        bc.test_stored("BINANCE_GLOBAL")
    except Exception:
        pass
    finally:
        bc._TESTERS["BINANCE_GLOBAL"] = orig
    assert sc.configured("BINANCE_GLOBAL")  # geçici hata SİLMEDİ
    r = bc.disconnect("BINANCE_GLOBAL")
    assert r["status"] == "DISCONNECTED" and r["removed"] is True
    assert not sc.configured("BINANCE_GLOBAL")


def test_no_secret_anywhere_in_data_dir(iso):
    """Secret hiçbir snapshot/audit/durum dosyasında düz metin değil."""
    _store_global()
    bc.audit("connection_attempt", "BINANCE_GLOBAL",
             sc.masked_key("BINANCE_GLOBAL"))
    bc._save_snapshot("BINANCE_GLOBAL", {"status": "CONNECTED_READ_ONLY"})
    for f in (iso / "data").iterdir():
        if f.name == "exchange_credentials.json" and os.name == "nt":
            continue  # nt'de DPAPI blob; düz metin zaten değil
        if f.name == "exchange_credentials.json":
            continue  # nt dışı yerel depo (test ortamı) — kapsam dışı
        content = f.read_text(encoding="utf-8", errors="replace")
        assert SEC not in content, f"secret sızıntısı: {f.name}"


def test_connect_accounts_preserves_existing(iso, monkeypatch, capsys):
    """SETUP connect_accounts: kayıtlı hesap için anahtar İSTEMEZ."""
    _store_global()
    sc.store("BINANCE_TR", KEY, SEC)
    monkeypatch.setattr(bc, "test_stored", lambda p: {
        "provider": p, "status": "CONNECTED_READ_ONLY"})

    def no_input(*a, **k):  # input çağrılırsa test düşer
        raise AssertionError("SETUP mevcut bağlantı için anahtar istedi")
    monkeypatch.setattr("builtins.input", no_input)
    wsf.report.clear()
    wsf.connect_accounts()
    out = capsys.readouterr().out
    assert "ZATEN YAPILANDIRILMIS" in out
    assert wsf.report["BINANCE GLOBAL ACCOUNT"] == "CONNECTED"
    assert wsf.report["BINANCE TR ACCOUNT"] == "CONNECTED"
    assert sc.configured("BINANCE_GLOBAL") and sc.configured("BINANCE_TR")


def test_connect_accounts_failed_test_keeps_credential(iso, monkeypatch,
                                                       capsys):
    """SETUP: bağlantı testi başarısız → yalnız kod gösterilir, silinmez."""
    _store_global()
    monkeypatch.setattr(bc, "test_stored", lambda p: {
        "provider": p, "status": "NETWORK_DEGRADED"})
    monkeypatch.setattr("builtins.input",
                        lambda *a, **k: (_ for _ in ()).throw(EOFError()))
    wsf.report.clear()
    wsf.connect_accounts()
    out = capsys.readouterr().out
    assert "KORUNDU" in out
    assert "PRESERVED" in wsf.report["BINANCE GLOBAL ACCOUNT"]
    assert sc.configured("BINANCE_GLOBAL")


def test_runtime_status_reports_config_sources(monkeypatch, tmp_path):
    """Windows Runtime status: ayar kaynakları panelde gösterilir."""
    from services import windows_runtime_recovery as wrr
    monkeypatch.setattr(wrr, "SNAPSHOT_PATH", tmp_path / "wr.json")
    monkeypatch.setattr(wrr, "live_health", lambda: {})
    st = wrr.status()
    assert st["live_orders"] == "DISABLED"
    assert st["config_sources"] == {"runtime_settings": ".env",
                                    "credentials": "windows_dpapi",
                                    "code": "github_main"}


def test_dpapi_fail_closed_on_other_machine(iso, monkeypatch):
    """DPAPI blob çözülemezse (başka makine/kullanıcı) fail-closed."""
    xc.FILE.write_text(json.dumps({
        "schema_version": 1,
        "accounts": {"BINANCE_GLOBAL": {
            "enc": "dpapi", "api_key_enc": "QUJD", "api_secret_enc": "QUJD",
        }}}), encoding="utf-8")
    assert sc.credentials("BINANCE_GLOBAL") == ("", "")
    assert not sc.configured("BINANCE_GLOBAL")
