"""429/418 geri çekilme durumunun süreçler arası paylaşımı.

Bot süreci ile gunicorn worker'ları ayrı süreçlerdir; yasak yalnız
süreç içi bellekte kalırsa bir süreç 418 görürken diğerleri istek
atmaya devam eder. Bu testler, iki ayrı "süreç"i (aynı dosyadan
bağımsız yüklenmiş taze modül kopyaları) simüle ederek yasağın
paylaşımlı dosya üzerinden görüldüğünü doğrular.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# app.py gibi: alpha20_v1 kopyası kökteki eski kopyayı gölgeler.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "alpha20_v1"))

import alpha20  # noqa: E402

ALPHA20_PATH = Path(alpha20.__file__).resolve()


def _fresh_alpha20(name: str, state_path: Path):
    """alpha20 modülünün bağımsız (taze bellek durumu) bir kopyasını yükler.

    Ayrı bir gunicorn worker / bot sürecini simüle eder: modül durumu
    sıfırdır, yalnız paylaşımlı dosya ortaktır.
    """
    spec = importlib.util.spec_from_file_location(name, ALPHA20_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    module.RATE_LIMIT_STATE_PATH = state_path
    return module


@pytest.fixture()
def state_path(tmp_path, monkeypatch):
    path = tmp_path / "rate_limit_state.json"
    monkeypatch.setattr(alpha20, "RATE_LIMIT_STATE_PATH", path,
                        raising=False)
    return path


class FakeResponse:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})


class TestSharedAcrossProcesses:
    def test_418_ban_seen_by_fresh_process(self, state_path):
        proc_a = _fresh_alpha20("alpha20_proc_a", state_path)
        proc_b = _fresh_alpha20("alpha20_proc_b", state_path)

        wait = proc_a.register_rate_limit(418)
        assert wait == proc_a.RATE_LIMIT_BAN_BACKOFF

        # Taze süreç, kendi belleğinde hiçbir şey olmamasına rağmen
        # yasağı paylaşımlı dosyadan görür.
        assert proc_b.rate_limit_remaining() > 0
        assert "IP yasağı" in proc_b.rate_limit_reason()

    def test_429_backoff_seen_by_fresh_process(self, state_path):
        proc_a = _fresh_alpha20("alpha20_proc_a", state_path)
        proc_b = _fresh_alpha20("alpha20_proc_b", state_path)

        proc_a.register_rate_limit(429)
        remaining = proc_b.rate_limit_remaining()
        assert 0 < remaining <= proc_a.RATE_LIMIT_DEFAULT_BACKOFF
        assert "429" in proc_b.rate_limit_reason()

    def test_main_module_sees_ban_from_other_process(self, state_path):
        proc_b = _fresh_alpha20("alpha20_proc_b", state_path)
        proc_b.register_rate_limit(418)
        assert alpha20.rate_limit_remaining() > 0
        assert "IP yasağı" in alpha20.rate_limit_reason()

    def test_consecutive_429_counter_escalates_across_processes(
            self, state_path):
        proc_a = _fresh_alpha20("alpha20_proc_a", state_path)
        proc_b = _fresh_alpha20("alpha20_proc_b", state_path)

        w1 = proc_a.register_rate_limit(429)
        # Diğer süreçteki ikinci 429 artan beklemeyi sürdürmeli
        # (yasak penceresini test için sıfırla; sayaç dosyada durur).
        data = json.loads(state_path.read_text())
        data["blocked_until"] = 0.0
        state_path.write_text(json.dumps(data))
        proc_a._rate_limit_state["blocked_until"] = 0.0
        w2 = proc_b.register_rate_limit(429)
        assert w1 == alpha20.RATE_LIMIT_DEFAULT_BACKOFF
        assert w2 == alpha20.RATE_LIMIT_DEFAULT_BACKOFF * 2

    def test_success_resets_counter_in_shared_file(self, state_path):
        proc_a = _fresh_alpha20("alpha20_proc_a", state_path)
        proc_a.register_rate_limit(429)
        assert json.loads(state_path.read_text())["consecutive_429"] == 1

        proc_b = _fresh_alpha20("alpha20_proc_b", state_path)
        proc_b.note_rate_limit_success()
        assert json.loads(state_path.read_text())["consecutive_429"] == 0

    def test_longer_block_is_never_shortened(self, state_path):
        proc_a = _fresh_alpha20("alpha20_proc_a", state_path)
        proc_b = _fresh_alpha20("alpha20_proc_b", state_path)

        proc_a.register_rate_limit(418, FakeResponse({"Retry-After": "600"}))
        before = proc_b.rate_limit_remaining()
        # Kısa bir 429 mevcut uzun yasağı KISALTMAMALI.
        proc_b.register_rate_limit(429, FakeResponse({"Retry-After": "5"}))
        after = proc_b.rate_limit_remaining()
        assert after >= before - 1.0
        assert "IP yasağı" in proc_b.rate_limit_reason()

    def test_reset_clears_shared_file(self, state_path):
        alpha20.register_rate_limit(418)
        assert state_path.exists()
        alpha20.reset_rate_limit_state()
        data = json.loads(state_path.read_text())
        assert data["blocked_until"] == 0.0
        proc_b = _fresh_alpha20("alpha20_proc_b", state_path)
        assert proc_b.rate_limit_remaining() == 0.0


class TestSharedFileRobustness:
    def test_corrupt_shared_file_falls_back_to_memory(self, state_path):
        state_path.write_text("{bozuk json", encoding="utf-8")
        # Okuma çökmez; süreç içi durum kullanılır.
        assert alpha20.rate_limit_remaining() == 0.0
        # Yeni 429 kaydı bozuk dosyayı geçerli içerikle değiştirir.
        alpha20.register_rate_limit(429)
        data = json.loads(state_path.read_text())
        assert data["blocked_until"] > 0

    def test_missing_file_means_no_block(self, state_path):
        state_path.unlink(missing_ok=True)
        assert alpha20.rate_limit_remaining() == 0.0
        assert alpha20.rate_limit_reason() == ""

    def test_write_failure_still_blocks_locally(self, state_path,
                                                monkeypatch):
        alpha20.register_rate_limit(418)
        # Dosya kaybolsa bile süreç içi yedek yasağı korur.
        state_path.unlink()
        assert alpha20.rate_limit_remaining() > 0
