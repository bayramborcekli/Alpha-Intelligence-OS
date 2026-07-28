# -*- coding: utf-8 -*-
"""Ekranlar arası tek kanonik snapshot sözleşmesi (ROOT BUG regresyonu).

GERÇEK WINDOWS BUG'ı: Hesaplarım "Bağlı" gösterirken Genel Bakış aynı
hesap için "Bağlantı Yok / anahtar yapılandırılmamış" gösteriyordu —
iki ekran aynı hesap için farklı backend state üretiyordu.

Zorunlu kabul:
  Settings Global HEALTHY  => Overview Global aynı kanonik snapshot HEALTHY
  Settings TR HEALTHY      => Overview TR aynı kanonik snapshot HEALTHY
  Overview kendi credential kontrolünü / health check'ini / kendi
  Binance account fetch'ini YAPMAZ.
Aynı snapshot jenerasyonu için iki ekranın farklı state göstermesi
TEST FAIL'dir.
"""
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard_api as dapi

ROOT = Path(__file__).resolve().parent.parent

GLOBAL_RAW = {
    "balances": [{"asset": "USDT", "free": "100.0", "locked": "0.0"}],
    "canTrade": True, "canWithdraw": False, "canDeposit": False,
}
TR_RAW = {"code": 0, "data": {"accountAssets": [
    {"asset": "USDT", "free": "50.0", "locked": "0.0"},
    {"asset": "TRY", "free": "0", "locked": "0"},
]}}


def _login(client):
    with client.session_transaction() as s:
        s["authenticated"] = True
        s["username"] = "test"


@pytest.fixture
def client():
    import app as flask_app
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh_caches():
    dapi.invalidate_caches()
    yield
    dapi.invalidate_caches()


def _prices_ok(*_a, **_k):
    return {"USDTUSDT": None}


class _Ctx:
    """Ham hesap katmanını sabitler: her iki borsa da tek snapshot."""

    def __init__(self, global_exc=None, tr_exc=None):
        self.global_exc = global_exc
        self.tr_exc = tr_exc

    def __enter__(self):
        def g_raw():
            if self.global_exc:
                raise self.global_exc
            return (GLOBAL_RAW, 5)

        def t_raw():
            if self.tr_exc:
                raise self.tr_exc
            return (TR_RAW, 5)

        self.patches = [
            patch.object(dapi, "_spot_account_raw", side_effect=g_raw),
            patch.object(dapi, "_tr_account_raw", side_effect=t_raw),
            patch.object(dapi, "_global_creds",
                         return_value=("G" * 20, "S" * 20)),
            patch.object(dapi, "_tr_creds",
                         return_value=("T" * 20, "U" * 20)),
            patch.object(dapi, "_spot_price_map", _prices_ok,
                         create=True),
        ]
        for p in self.patches:
            try:
                p.start()
            except AttributeError:
                self.patches.remove(p)
        return self

    def __exit__(self, *a):
        for p in self.patches:
            try:
                p.stop()
            except RuntimeError:
                pass
        return False


def _states(client):
    """Aynı snapshot jenerasyonunda üç ekranın state'lerini toplar."""
    # 1) Hesaplarım (Settings)
    r = client.get("/api/accounts")
    assert r.status_code == 200
    cards = {a["exchange"]: a for a in r.get_json()["data"]["accounts"]}
    # 2) Genel Bakış (Overview API)
    r2 = client.get("/api/v1/overview")
    assert r2.status_code == 200
    ov = r2.get_json()
    # 3) Yönetici şeridi
    import executive_api as ea
    summ = ea.executive_summary(False, "PAPER")
    return cards, ov, summ["status_bar"]


EXEC_MAP = {"HEALTHY": "Bağlı", "STALE": "Kısmi"}


def _assert_consistent(cards, ov, bar):
    pairs = [("BINANCE_GLOBAL", "global_spot", "binance_global"),
             ("BINANCE_TR", "tr", "binance_tr")]
    for reg_ex, ov_key, bar_key in pairs:
        card = cards.get(reg_ex)
        if card is None or not card["connected"]:
            continue
        s_settings = card["connection_state"]
        s_overview = ov[ov_key]["connection_state"]
        assert s_settings == s_overview, (
            f"{reg_ex}: Hesaplarım={s_settings} != "
            f"Genel Bakış={s_overview} — aynı snapshot jenerasyonu için "
            "iki ekran farklı state gösteremez (ROOT BUG)")
        expected_bar = EXEC_MAP.get(s_overview, "Bağlantı Yok")
        assert bar[bar_key] == expected_bar, (
            f"{reg_ex}: yönetici şeridi={bar[bar_key]} != {expected_bar} "
            f"(kanonik state={s_overview})")


class TestSameSnapshotSameState:
    def test_healthy_everywhere(self, client):
        _login(client)
        with _Ctx():
            cards, ov, bar = _states(client)
        _assert_consistent(cards, ov, bar)
        assert ov["global_spot"]["connection_state"] == "HEALTHY"
        assert ov["tr"]["connection_state"] == "HEALTHY"

    def test_not_configured_everywhere(self, client):
        _login(client)
        exc_g = dapi.SafeExchangeError(
            "NOT_CONFIGURED", dapi.ERROR_MESSAGES["NOT_CONFIGURED"])
        exc_t = dapi.SafeExchangeError(
            "NOT_CONFIGURED", dapi.ERROR_MESSAGES["NOT_CONFIGURED"])
        with _Ctx(global_exc=exc_g, tr_exc=exc_t):
            cards, ov, bar = _states(client)
        _assert_consistent(cards, ov, bar)
        # Hesaplarım "Bağlı" gösterirken Genel Bakış NOT_CONFIGURED
        # gösteremez — kart da kanonik state'i taşımalı.
        for ex in ("BINANCE_GLOBAL", "BINANCE_TR"):
            if cards.get(ex, {}).get("connected"):
                assert cards[ex]["connection_state"] == "NOT_CONFIGURED"

    def test_auth_failed_everywhere(self, client):
        _login(client)
        exc = dapi.SafeExchangeError(
            "EXCHANGE_AUTH_FAILED",
            dapi.ERROR_MESSAGES["EXCHANGE_AUTH_FAILED"])
        with _Ctx(global_exc=exc):
            cards, ov, bar = _states(client)
        _assert_consistent(cards, ov, bar)
        assert ov["global_spot"]["connection_state"] == "AUTH_FAILED"


class TestSingleDerivation:
    """Ekranlar kendi state türetimini/health check'ini yapamaz."""

    def test_app_delegates_to_canonical(self):
        import app as flask_app
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        m = re.search(r"def _connection_state\(.*?\n(?=\ndef )", src,
                      re.S)
        assert m and "dapi.connection_state(res)" in m.group(0), (
            "app._connection_state kanonik dashboard_api."
            "connection_state'e delege etmeli")
        assert flask_app._connection_state({"ok": True, "meta": {}}) == \
            dapi.connection_state({"ok": True, "meta": {}})

    def test_executive_has_no_own_health_logic(self):
        src = (ROOT / "executive_api.py").read_text(encoding="utf-8")
        assert "connection_state" in src
        # Kendi freshness/ok tabanlı türetim kalmadı:
        assert 'freshness") or "").upper()' not in src

    def test_overview_template_reads_canonical_field(self):
        src = (ROOT / "templates" / "overview.html").read_text(
            encoding="utf-8")
        assert "connection_state" in src, (
            "Genel Bakış bağlantı metnini kanonik connection_state "
            "alanından okumalı")

    def test_my_accounts_badge_reads_canonical_field(self):
        src = (ROOT / "static" / "js" / "my_accounts.js").read_text(
            encoding="utf-8")
        assert "connection_state" in src, (
            "Hesaplarım rozeti kanonik connection_state alanından "
            "okunmalı")

    def test_ui_never_falls_back_to_bagli(self):
        # connection_state alanı eksikse UI yanlış pozitif "Bağlı"
        # gösteremez (Durum Bilinmiyor'a düşer).
        js = (ROOT / "static" / "js" / "my_accounts.js").read_text(
            encoding="utf-8")
        html = (ROOT / "templates" / "overview.html").read_text(
            encoding="utf-8")
        # Rozet zincirinin son (fallback) dalı "Bağlı" olamaz.
        chain = js.split("var statusBadge")[1].split(";")[0]
        last_branch = chain.rsplit(":", 1)[1]
        assert "Bağlı\"" not in last_branch and \
            "Durum Bilinmiyor" in last_branch
        assert "Durum Bilinmiyor" in js
        assert "Durum Bilinmiyor" in html

    def test_models_carry_connection_state(self):
        with _Ctx():
            gs = dapi.global_spot_account()
            ta = dapi.tr_account()
        assert gs["connection_state"] == "HEALTHY"
        assert ta["connection_state"] == "HEALTHY"
