"""GÖREV 118 EK — Kontrollü PAPER_LEARNING profili testleri.

Sözleşme:
- STRICT davranış (relax kapalı) birebir DEĞİŞMEZ.
- Esnetilen TEK yumuşak kapı: EMA/VWAP birleşik ön koşulu
  (ret hunisinde kanıtlanan baskın kapı; 300 retin 212'si).
  İki koşuldan EN AZ BİRİ hâlâ zorunlu; ikisi de yoksa NO_SIGNAL.
- Sert kapılar (veri, likidite, spread/slippage, NET_TP, NET_RR,
  EDGE_COST, risk/limit/duplicate/cooldown) AYNEN uygulanır.
- LIVE yolu yok; işlem yalnız PAPER, profile etiketiyle izlenir.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    monkeypatch.setattr(dm, "LEGACY_STATE_PATH", tmp_path / "state.json")
    yield


CFG = json.loads(json.dumps(dm.DEFAULTS))
CFG["core"]["config_version"] = "BASE"
CFG["opportunity"]["config_version"] = "BASE"


def _klines_one_block(n=60, above_vwap=True):
    """EMA aşağı (ema9<ema21) ama fiyat VWAP üstünde biten seri —
    yalnız EMA bloklu aday (VWAP koşulu sağlanır)."""
    out = []
    price = 100.0
    for i in range(n):
        # Uzun düşüş → ema9 < ema21
        price *= 0.999
        v = 1000.0 * (3.0 if i >= n - 5 else 1.0)
        out.append([0, str(price), str(price * 1.001),
                    str(price * 0.998), str(price), str(v)])
    if above_vwap:
        # Son mumda sıçrama: fiyat pencere VWAP'ının üstüne çıkar ama
        # EMA9 hâlâ EMA21 altında kalır (tek blok: EMA)
        last = out[-1][:]
        last[4] = str(price * 1.02)
        out[-1] = last
    return out


def _klines_both_block(n=60):
    """Hem EMA hem VWAP bloklu (düşüş, fiyat VWAP altında)."""
    out, price = [], 100.0
    for i in range(n):
        price *= 0.998
        out.append([0, str(price), str(price * 1.001),
                    str(price * 0.998), str(price), str(1000.0)])
    return out


def _row(**kw):
    r = {"spread_pct": 0.005, "volume_usdt": 200e6,
         "trade_count": 300000}
    r.update(kw)
    return r


class TestStrictUnchanged:
    def test_default_call_identical_to_before(self):
        kl = _klines_one_block()
        strict = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE)
        assert strict["side"] is None
        assert strict["reason_code"] == "NO_SIGNAL"
        assert "profile" not in strict
        # relax=False açıkça geçildiğinde de birebir aynı
        assert strict == dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                            relax_ema_vwap=False)


class TestRelaxedGate:
    def test_single_block_becomes_tagged_candidate(self):
        kl = _klines_one_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        assert lsig["side"] == "LONG"
        assert lsig["profile"] == "PAPER_LEARNING"
        assert lsig["relaxed_gate"] == "EMA_VWAP_COMBINED"

    def test_both_blocked_stays_no_signal(self):
        kl = _klines_both_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        assert lsig["side"] is None
        assert lsig["reason_code"] == "NO_SIGNAL"
        assert lsig["sub_reason"] == "EMA_VWAP_BLOCK"

    def test_no_confidence_inflation(self):
        """Esnetilmiş aday, tam koşullu adaydan DAHA YÜKSEK taban
        puan alamaz (aynı +30 taban, aynı bonuslar)."""
        kl = _klines_one_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        assert lsig["confidence"] <= 100
        assert lsig["confidence"] >= 30  # taban aynı


class TestHardGatesImmutable:
    def test_learning_candidate_still_dies_at_net_rr(self):
        """SERT KAPI KANITI: varsayılan CORE profiliyle esnetilmiş
        aday bile NET_REWARD_RISK kapısında ölür (kapı gevşetilmedi).
        Bu, mevcut yapısal çelişkinin PAPER_LEARNING'de de dürüstçe
        korunduğunun regresyon mühürüdür."""
        kl = _klines_one_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        lsig["expected_gross_edge_pct"] = 1.9  # edge sorunu dışarıda
        ok, reason, _ = dm.execution_quality_gate(
            _row(), lsig, dm.MODEL_CORE, CFG)
        assert not ok
        assert reason == "NET_REWARD_RISK_TOO_LOW"

    def test_liquidity_gate_still_applies(self):
        kl = _klines_one_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        ok, reason, _ = dm.execution_quality_gate(
            _row(volume_usdt=1e4), lsig, dm.MODEL_CORE, CFG)
        assert not ok and reason == "LOW_LIQUIDITY"


class TestPaperOnlyOpenAndJournal:
    def _passable_cfg(self):
        cfg = json.loads(json.dumps(CFG))
        cfg["core"]["tp_pct"] = 1.0  # test-cfg: RR kapısı geçilebilir
        return cfg

    def test_position_and_trade_tagged_with_profile(self):
        cfg = self._passable_cfg()
        kl = _klines_one_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        lsig["expected_gross_edge_pct"] = 1.9
        ok, reason, net = dm.execution_quality_gate(
            _row(), lsig, dm.MODEL_CORE, cfg)
        assert ok, reason
        opened, why = dm.try_open_position("XUSDT", dm.MODEL_CORE,
                                           lsig, net, cfg, now=1000.0)
        assert opened, why
        rt = json.load(open(dm.RUNTIME_PATH))
        p = rt["positions"]["XUSDT"]
        assert p["profile"] == "PAPER_LEARNING"
        assert p["relaxed_gate"] == "EMA_VWAP_COMBINED"
        assert p["execution_mode"] == "PAPER"
        entry = p["entry"]
        tp = entry * (1 + cfg["core"]["tp_pct"] / 100)
        closed = dm.monitor_positions(lambda s: tp, cfg, now=1100.0)
        assert len(closed) == 1
        assert closed[0]["profile"] == "PAPER_LEARNING"
        assert closed[0]["relaxed_gate"] == "EMA_VWAP_COMBINED"
        assert closed[0]["net_pnl"] is not None

    def test_strict_position_tagged_strict(self):
        sig = {"side": "LONG", "last": 100.0, "confidence": 80}
        ok, _ = dm.try_open_position("YUSDT", dm.MODEL_CORE, sig,
                                     0.5, CFG, now=1000.0)
        assert ok
        rt = json.load(open(dm.RUNTIME_PATH))
        assert rt["positions"]["YUSDT"]["profile"] == "STRICT"
        assert rt["positions"]["YUSDT"]["relaxed_gate"] is None

    def test_learning_journal_fields(self):
        kl = _klines_one_block()
        strict = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE)
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        dm.record_learning_decision(
            "XUSDT", dm.MODEL_CORE, strict, lsig, False,
            "NET_REWARD_RISK_TOO_LOW", 0.9, CFG, False, None,
            btc_change_pct=1.2)
        rt = json.load(open(dm.RUNTIME_PATH))
        j = rt["learning_journal"]
        assert len(j) == 1
        e = j[0]
        assert e["profile"] == "PAPER_LEARNING"
        assert e["strict_decision"] == "NO_SIGNAL"
        assert e["learning_decision"] == "NET_REWARD_RISK_TOO_LOW"
        assert e["relaxed_gate"] == "EMA_VWAP_COMBINED"
        for f in ("entry_quality_score", "expected_edge",
                  "net_reward_risk", "round_trip_cost",
                  "market_regime", "at", "symbol", "model"):
            assert f in e

    def test_journal_capped_at_500(self):
        kl = _klines_one_block()
        strict = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE)
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        for _ in range(505):
            dm.record_learning_decision(
                "XUSDT", dm.MODEL_CORE, strict, lsig, False,
                "X", 0.0, CFG, False, None)
        rt = json.load(open(dm.RUNTIME_PATH))
        assert len(rt["learning_journal"]) == 500


class TestConfigWiring:
    def test_defaults_disabled_and_config_overlay(self):
        assert dm.DEFAULTS["paper_learning"]["enabled"] is False
        cfg = dm.get_config({"dual_model": {
            "paper_learning": {"enabled": True}}})
        assert cfg["paper_learning"]["enabled"] is True
        assert cfg["paper_learning"]["relaxed_gate"] == \
            "EMA_VWAP_COMBINED"


class TestOwnershipArbitration:
    def test_learning_candidate_loses_to_higher_net_edge(self):
        """Öğrenme adayı sahiplik arbitrajını ATLAYAMAZ: aynı sembol
        için diğer modelin STRICT adayı daha yüksek net edge ile
        kazanır; öğrenme adayı DUPLICATE_MODEL_OWNERSHIP olur."""
        lsig = {"side": "LONG", "last": 100.0, "confidence": 70,
                "profile": "PAPER_LEARNING",
                "relaxed_gate": "EMA_VWAP_COMBINED"}
        ssig = {"side": "LONG", "last": 100.0, "confidence": 80}
        own = dm.resolve_ownership({
            dm.MODEL_CORE: [{"symbol": "ZUSDT", "sig": lsig,
                             "net_edge_pct": 0.3,
                             "learning_strict": {"reason_code":
                                                 "NO_SIGNAL"}}],
            dm.MODEL_OPP: [{"symbol": "ZUSDT", "sig": ssig,
                            "net_edge_pct": 0.9}],
        })
        assert len(own["winners"]) == 1
        assert own["winners"][0]["model"] == dm.MODEL_OPP
        assert own["rejected"][0]["sig"]["profile"] == "PAPER_LEARNING"
