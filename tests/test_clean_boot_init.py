# -*- coding: utf-8 -*-
"""MISSION — CLEAN BOOT INITIALIZATION regresyonu.

Temiz clone'da alpha20_v1/state.json (PAPER defteri) yoktur ve Kağıt
Hesap kartı yanlış CONNECTION_FAILED gösteriyordu. app._ensure_paper_state
ilk açılışta güvenli başlangıç defterini oluşturur; .env yoksa local_env
açık Türkçe kurulum uyarısı loglar.
"""
import json
import logging
from unittest.mock import patch

import pytest

import app as appmod
import local_env

PAPER_CFG = {"mode": "PAPER", "starting_balance_usdt": 10000.0,
             "symbols": ["BTCUSDT"]}


def _run(tmp_path, cfg):
    state = tmp_path / "state.json"
    cfgf = tmp_path / "config.json"
    if cfg is not None:
        cfgf.write_text(json.dumps(cfg), encoding="utf-8")
    with patch.object(appmod, "STATE_PATH", state), \
         patch.object(appmod, "CONFIG_PATH", cfgf):
        appmod._ensure_paper_state()
    return state


class TestEnsurePaperState:
    def test_creates_initial_ledger(self, tmp_path):
        state = _run(tmp_path, PAPER_CFG)
        assert state.exists()
        data = json.loads(state.read_text(encoding="utf-8"))
        assert data["balance"] == 10000.0
        assert data["day_start_balance"] == 10000.0
        assert data["position"] is None
        assert data["trades"] == []
        assert data["consecutive_losses"] == 0

    def test_paper_balance_healthy_after_init(self, tmp_path):
        state = _run(tmp_path, PAPER_CFG)
        with patch.object(appmod, "STATE_PATH", state):
            assert appmod._paper_balance() == "10000.0"
            snap = appmod._account_snapshot("PAPER")
        assert snap["connection_state"] == "HEALTHY"
        assert snap["status"] == "OK"

    def test_never_overwrites_existing_ledger(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"balance": 123.45}), encoding="utf-8")
        cfgf = tmp_path / "config.json"
        cfgf.write_text(json.dumps(PAPER_CFG), encoding="utf-8")
        with patch.object(appmod, "STATE_PATH", state), \
             patch.object(appmod, "CONFIG_PATH", cfgf):
            appmod._ensure_paper_state()
        assert json.loads(state.read_text(encoding="utf-8"))["balance"] == 123.45

    def test_fail_closed_non_paper_mode(self, tmp_path):
        state = _run(tmp_path, {"mode": "LIVE",
                                "starting_balance_usdt": 10000.0})
        assert not state.exists()

    def test_fail_closed_missing_config(self, tmp_path):
        state = _run(tmp_path, None)
        assert not state.exists()

    def test_fail_closed_bad_balance(self, tmp_path):
        state = _run(tmp_path, {"mode": "PAPER"})
        assert not state.exists()

    @pytest.mark.parametrize("bad_cfg", [[], "PAPER", 42])
    def test_fail_closed_non_dict_config_no_crash(self, tmp_path, bad_cfg):
        # Geçerli JSON ama dict değil → import-time crash YOK, dosya YOK
        state = _run(tmp_path, bad_cfg)
        assert not state.exists()


class TestEnvMissingWarning:
    def _load(self, tmp_path, caplog, with_file):
        f = tmp_path / ".env"
        if with_file:
            f.write_text("X=1\n", encoding="utf-8")
        with patch.object(local_env, "ENV_FILE", f), \
             patch.object(local_env, "is_replit", return_value=False), \
             caplog.at_level(logging.WARNING, logger="local_env"):
            local_env.load_project_env(force=True)
        local_env.reset_for_tests()
        return caplog.text

    def test_warns_when_env_missing(self, tmp_path, caplog):
        text = self._load(tmp_path, caplog, with_file=False)
        assert "İLK KURULUM" in text
        assert ".env.example" in text

    def test_silent_when_env_exists(self, tmp_path, caplog):
        text = self._load(tmp_path, caplog, with_file=True)
        assert "İLK KURULUM" not in text
