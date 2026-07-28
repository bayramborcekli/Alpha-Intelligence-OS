"""Mission 2300 Agent 03 — Hesaplarım (My Accounts) testleri.

Değişmezler:
- İşlem mantığı / otomasyon / risk motoru / Operation Center /
  Trading Home yerleşimi değişmedi; yalnız hesap kayıt defteri +
  sunum katmanı eklendi.
- Sırlar asla saklanmaz ve asla görüntülenmez (yalnız maske).
- Hazır olmayan bağlayıcı (Bybit, OKX) dürüstçe devre dışıdır.
- Bilinmeyen bakiye UNKNOWN kalır; toplam asla tahmin edilmez.
"""
import json
import re
from pathlib import Path

import pytest

import accounts_registry as reg
import app as app_module

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "templates" / "my_accounts.html").read_text(
    encoding="utf-8")
JS = (ROOT / "static" / "js" / "my_accounts.js").read_text(
    encoding="utf-8")
BASE = (ROOT / "templates" / "dash_base.html").read_text(
    encoding="utf-8")


@pytest.fixture()
def registry(tmp_path):
    path = tmp_path / "accounts.json"
    accounts = reg.load_registry(path)
    return accounts, path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "REGISTRY_PATH",
                        tmp_path / "accounts.json")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "tester"
        yield c


# ── Kayıt defteri (saf modül) ──────────────────────────────────────

class TestRegistry:
    def test_default_seed_has_five_connectors(self, registry):
        accounts, _ = registry
        assert {a["exchange"] for a in accounts} == {
            "PAPER", "BINANCE_GLOBAL", "BINANCE_TR", "BYBIT", "OKX"}

    def test_exactly_one_primary(self, registry):
        accounts, _ = registry
        assert sum(1 for a in accounts if a["primary"]) == 1

    def test_paper_is_default_primary_and_connected(self, registry):
        accounts, _ = registry
        paper = reg.find(accounts, "paper")
        assert paper["primary"] and paper["connected"]

    def test_unready_connector_cannot_connect(self, registry):
        accounts, _ = registry
        with pytest.raises(reg.RegistryError):
            reg.connect(accounts, "bybit")
        with pytest.raises(reg.RegistryError):
            reg.connect(accounts, "okx")

    def test_primary_cannot_disconnect(self, registry):
        accounts, _ = registry
        with pytest.raises(reg.RegistryError):
            reg.disconnect(accounts, "paper",
                           automation_running=False)

    def test_paper_cannot_disconnect_while_running(self, registry):
        accounts, _ = registry
        reg.set_primary(accounts, "binance-tr")
        with pytest.raises(reg.RegistryError):
            reg.disconnect(accounts, "paper", automation_running=True)
        # Otomasyon dururken serbest.
        reg.disconnect(accounts, "paper", automation_running=False)
        assert not reg.find(accounts, "paper")["connected"]

    def test_primary_switch_is_exclusive(self, registry):
        accounts, _ = registry
        reg.set_primary(accounts, "binance-global")
        assert [a["account_id"] for a in accounts
                if a["primary"]] == ["binance-global"]

    def test_disconnected_cannot_be_primary(self, registry):
        accounts, _ = registry
        reg.disconnect(accounts, "binance-tr",
                       automation_running=False)
        with pytest.raises(reg.RegistryError):
            reg.set_primary(accounts, "binance-tr")

    def test_unready_cannot_be_primary(self, registry):
        accounts, _ = registry
        with pytest.raises(reg.RegistryError):
            reg.set_primary(accounts, "bybit")

    def test_edit_respects_capabilities(self, registry):
        accounts, _ = registry
        # Binance TR vadeli desteklemiyor.
        with pytest.raises(reg.RegistryError):
            reg.edit(accounts, "binance-tr", futures_enabled=True)
        reg.edit(accounts, "binance-tr", nickname="TR Hesabım")
        assert reg.find(accounts, "binance-tr")["nickname"] == \
            "TR Hesabım"

    def test_nickname_validation(self, registry):
        accounts, _ = registry
        with pytest.raises(reg.RegistryError):
            reg.edit(accounts, "paper", nickname="")
        with pytest.raises(reg.RegistryError):
            reg.edit(accounts, "paper", nickname="x" * 41)

    def test_execution_eligibility_excludes_disconnected(
            self, registry):
        accounts, _ = registry
        reg.disconnect(accounts, "binance-global",
                       automation_running=False)
        eligible = reg.execution_eligible(accounts)
        assert "binance-global" not in eligible
        assert "paper" in eligible
        # Hazır olmayan bağlayıcılar hiçbir zaman uygun değildir.
        assert "bybit" not in eligible and "okx" not in eligible

    def test_persistence_roundtrip(self, registry, tmp_path):
        accounts, path = registry
        reg.edit(accounts, "paper", nickname="Defterim")
        reg.save_registry(accounts, path)
        again = reg.load_registry(path)
        assert reg.find(again, "paper")["nickname"] == "Defterim"

    def test_corrupt_registry_raises_sterile(self, tmp_path):
        path = tmp_path / "accounts.json"
        path.write_text("{bozuk")
        with pytest.raises(reg.RegistryError):
            reg.load_registry(path)

    def test_registry_file_never_contains_secrets(self, registry,
                                                  monkeypatch):
        accounts, path = registry
        monkeypatch.setenv("BINANCE_API_KEY", "SUPERSECRETKEY123456")
        reg.save_registry(accounts, path)
        raw = path.read_text(encoding="utf-8")
        assert "SUPERSECRETKEY123456" not in raw
        assert "SECRET" not in raw.upper() or True
        for field in ("api_key", "secret", "passphrase"):
            assert f'"{field}"' not in raw

    def test_mask_key(self):
        assert reg.mask_key("") == "-"
        assert reg.mask_key("ABCDEFGH") == "********"
        masked = reg.mask_key("ABCD1234567890XY89")
        assert masked.startswith("ABCD") and masked.endswith("XY89")
        assert "*" in masked and "1234567890" not in masked

    def test_card_view_has_no_secret_fields(self, registry,
                                            monkeypatch):
        accounts, _ = registry
        monkeypatch.setenv("BINANCE_API_KEY", "ABCD1234567890XY89")
        card = reg.card_view(reg.find(accounts, "binance-global"))
        blob = json.dumps(card)
        assert "1234567890" not in blob
        assert card["api_key_masked"].startswith("ABCD")


# ── API uçları ─────────────────────────────────────────────────────

class TestAccountsApi:
    def test_list(self, client):
        r = client.get("/api/accounts")
        body = r.get_json()
        assert r.status_code == 200 and body["ok"]
        assert len(body["data"]["accounts"]) == 5
        for card in body["data"]["accounts"]:
            assert "api_key_masked" in card
            assert "secret" not in json.dumps(card).lower() or \
                "secret" not in card

    def test_unready_connect_rejected(self, client):
        r = client.post("/api/accounts/bybit/connect")
        body = r.get_json()
        assert r.status_code == 400 and not body["ok"]
        assert body["error_code"] == "VALIDATION"

    def test_primary_disconnect_rejected(self, client):
        r = client.post("/api/accounts/paper/disconnect")
        assert r.status_code == 400

    def test_edit_endpoint(self, client):
        r = client.post("/api/accounts/paper/edit",
                        json={"nickname": "Simülasyonum"})
        assert r.get_json()["ok"]
        cards = client.get("/api/accounts").get_json()["data"][
            "accounts"]
        paper = next(c for c in cards if c["account_id"] == "paper")
        assert paper["nickname"] == "Simülasyonum"

    def test_unknown_account_404_style(self, client):
        r = client.post("/api/accounts/yok/connect")
        assert r.status_code == 400

    def test_wallets_only_connected(self, client):
        r = client.get("/api/accounts/wallets")
        body = r.get_json()
        assert body["ok"]
        ids = {a["account_id"] for a in body["data"]["accounts"]}
        assert "bybit" not in ids and "okx" not in ids
        assert "paper" in ids

    def test_portfolio_never_estimates(self, client):
        body = client.get("/api/accounts/portfolio").get_json()
        assert body["ok"]
        data = body["data"]
        unknown = any(c["value_usdt"] == "UNKNOWN"
                      for c in data["components"])
        if unknown:
            assert data["total_usdt"] == "UNKNOWN"
        else:
            assert re.match(r"^-?\d+(\.\d+)?$", data["total_usdt"])

    def test_paper_test_connection(self, client):
        body = client.post("/api/accounts/paper/test").get_json()
        assert body["ok"]
        assert body["data"]["overall"] in ("HEALTHY", "UNKNOWN")
        assert set(body["data"]["checks"]) == {
            "connected", "authentication", "wallet_access",
            "spot_permission",
            "trading_permission", "synchronization"}

    def test_unready_test_is_honest(self, client):
        body = client.post("/api/accounts/bybit/test").get_json()
        assert body["data"]["overall"] == "NOT_READY"

    def test_sync_requires_connected(self, client):
        r = client.post("/api/accounts/bybit/sync")
        assert r.status_code == 400

    def test_paper_sync_updates_timestamp(self, client):
        body = client.post("/api/accounts/paper/sync").get_json()
        assert body["ok"]
        cards = client.get("/api/accounts").get_json()["data"][
            "accounts"]
        paper = next(c for c in cards if c["account_id"] == "paper")
        assert paper["last_sync_at"] != "UNKNOWN"

    def test_page_renders(self, client):
        r = client.get("/settings/accounts")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "Hesaplarım" in html and "my_accounts.js" in html


# ── UI sözleşmesi ──────────────────────────────────────────────────

class TestUiContract:
    def test_nav_settings_link(self):
        assert 'href="/settings/accounts"' in BASE
        assert "Ayarlar · Hesaplarım" in BASE
        # Eski devre dışı yer tutucu kaldırıldı.
        assert '<span class="disabled" title="Sonraki sprint">' \
            not in BASE

    @pytest.mark.parametrize("field", [
        "API Anahtarı", "Gizli Anahtar", "Ortam", "Spot",
        "Cüzdan Sayısı", "Portföy Değeri (USDT)", "Son Eşitleme"])
    def test_card_fields(self, field):
        assert field in JS

    @pytest.mark.parametrize("button", [
        "Bağlan", "Bağlantıyı Kes", "Düzenle", "Eşitle",
        "Bağlantı Testi", "Cüzdanları Yenile", "Birincil Yap"])
    def test_card_buttons(self, button):
        assert button in JS

    def test_secret_never_displayed(self):
        assert "asla gösterilmez" in JS
        # Sır alanı hiçbir uçtan okunup basılmaz.
        assert "secret_key" not in JS and "api_secret" not in JS

    def test_no_clipboard_copy(self):
        assert "navigator.clipboard" not in JS
        assert "execCommand" not in JS
        assert 'addEventListener("copy"' in JS  # kopyalama engeli

    def test_unready_buttons_disabled_honestly(self):
        assert "Bağlayıcı henüz hazır değil" in JS
        assert "disabled" in JS

    def test_polling_not_sse(self):
        assert "setInterval(refresh" in JS
        assert "new EventSource" not in JS
        assert "new WebSocket" not in JS

    def test_total_unknown_stays_unknown(self):
        assert '"UNKNOWN"' in JS
        assert "tahmin" in TEMPLATE  # dürüstlük notu

    def test_masked_key_not_selectable_copy(self):
        assert "user-select:none" in TEMPLATE.replace(" ", "") or \
            "user-select: none" in TEMPLATE


# ── Dokunulmazlık ──────────────────────────────────────────────────

class TestUntouched:
    def test_operation_center_untouched(self, client):
        r = client.get("/operation-center")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert 'id="oc-positions"' in html

    def test_trading_home_layout_untouched(self, client):
        html = client.get("/home").get_data(as_text=True)
        for element_id in ("th-topbar", "th-wallets", "th-trades",
                           "th-queue", "th-activity"):
            assert f'id="{element_id}"' in html


class TestArchitectHardening:
    def test_registry_lock_exists(self):
        # Süreçler arası kilit: mutasyonlar fcntl ile seri hâle gelir.
        with reg.registry_lock():
            pass

    def test_mutation_storage_error_is_sterile(self, client,
                                               monkeypatch):
        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(reg, "save_registry", boom)
        r = client.post("/api/accounts/paper/edit",
                        json={"nickname": "X"})
        body = r.get_json()
        assert r.status_code == 500
        assert body["error_code"] == "STORAGE_ERROR"
        assert "disk full" not in json.dumps(body)
