"""GÖREV 118 EK — Kontrollü PAPER_LEARNING profili testleri.

Sözleşme:
- STRICT davranış (relax kapalı) birebir DEĞİŞMEZ.
- ADR-013 Paper giriş rotaları: STRICT_TREND,
  EMA_OR_VWAP_CONFIRMATION ve PRICE_MOMENTUM_PROBE.
- Kısa pencere mum hacmi Paper giriş engeli değildir; temel piyasa
  likiditesi ve maliyet kapıları sert kalır.
- Sert kapılar (veri, likidite, spread/slippage, pozitif-net hedef,
  risk/limit/duplicate/cooldown) AYNEN uygulanır. NET_RR 1.20 ve
  EDGE_COST çarpanı yalnız PAPER_LEARNING'de kalite hedefidir;
  STRICT'te sert kapı kalır.
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


def _klines_price_probe(n=60):
    """EMA+VWAP gerideyken fiyat toparlanması: hacim teyidi yoktur."""
    out, price = [], 100.0
    for i in range(n):
        price *= 1.004 if i >= n - 5 else 0.998
        out.append([0, str(price), str(price * 1.001),
                    str(price * 0.999), str(price), str(1000.0)])
    return out


def _klines_rising_low_candle_volume(n=60):
    """Fiyat güçlü yükselir; son beş mum hacmi tabanın yalnız %30'u."""
    out, price = [], 100.0
    for i in range(n):
        price *= 1.003
        if i == n - 1:
            price *= 0.998
        volume = 300.0 if i >= n - 5 else 1000.0
        out.append([0, str(price), str(price * 1.001),
                    str(price * 0.999), str(price), str(volume)])
    return out


def _klines_moderate_rise(n=60):
    """Beş dakikada yaklaşık %0.50 yükseliş: maliyet sonrası pozitif,
    fakat eski 1.5x ek maliyet tamponunun altında doğal Paper örneği."""
    out, price = [], 100.0
    for _ in range(n):
        price *= 1.001
        out.append([0, str(price), str(price * 1.001),
                    str(price * 0.999), str(price), "1000.0"])
    return out


def _klines_old_uptrend_but_recent_fall(n=60):
    """EMA/VWAP hâlâ yukarı görünürken son beş mum fiyatı düşer."""
    out, price = [], 100.0
    for i in range(n):
        price *= 0.998 if i >= n - 5 else 1.002
        out.append([0, str(price), str(price * 1.001),
                    str(price * 0.999), str(price), "1000.0"])
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

    def test_price_momentum_probe_opens_route_with_both_trend_blocks(self):
        kl = _klines_price_probe()
        strict = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE)
        paper = dm.evaluate_signal(
            "XUSDT", kl, dm.MODEL_CORE, relax_ema_vwap=True,
            profile="PAPER_LEARNING")

        assert strict["side"] is None
        assert strict["sub_reason"] == "EMA_VWAP_BLOCK"
        assert paper["side"] == "LONG"
        assert paper["entry_route"] == "PRICE_MOMENTUM_PROBE"
        assert paper["profile"] == "PAPER_LEARNING"

    def test_low_candle_volume_is_warning_not_paper_signal_blocker(self):
        kl = _klines_rising_low_candle_volume()
        strict = dm.evaluate_signal("XUSDT", kl, dm.MODEL_OPP)
        paper = dm.evaluate_signal(
            "XUSDT", kl, dm.MODEL_OPP, relax_ema_vwap=True,
            profile="PAPER_LEARNING")

        assert strict["side"] is None
        assert strict["reason_code"] == "MOMENTUM_EXHAUSTED"
        assert paper["side"] == "LONG"
        assert paper["vol_ratio"] == pytest.approx(0.3)
        assert "MOMENTUM_EXHAUSTED" in paper["quality_warnings"]

    def test_low_candle_volume_does_not_subtract_paper_edge(self):
        low = _klines_rising_low_candle_volume()
        flat = [list(k) for k in low]
        for k in flat:
            k[5] = "1000.0"

        low_sig = dm.evaluate_signal(
            "XUSDT", low, dm.MODEL_CORE, relax_ema_vwap=True,
            profile="PAPER_LEARNING")
        flat_sig = dm.evaluate_signal(
            "XUSDT", flat, dm.MODEL_CORE, relax_ema_vwap=True,
            profile="PAPER_LEARNING")

        assert low_sig["expected_gross_edge_pct"] == \
            flat_sig["expected_gross_edge_pct"]

    def test_recent_price_fall_never_becomes_positive_paper_edge(self):
        sig = dm.evaluate_signal(
            "XUSDT", _klines_old_uptrend_but_recent_fall(),
            dm.MODEL_CORE, relax_ema_vwap=True,
            profile="PAPER_LEARNING")

        assert sig["side"] == "LONG"  # eski trend izi hâlâ mevcut
        assert sig["expected_gross_edge_pct"] == 0

        _sig, ok, reason, _net = dm.evaluate_paper_learning_candidate(
            _row(), "XUSDT", _klines_old_uptrend_but_recent_fall(),
            dm.MODEL_CORE, CFG)
        assert not ok and reason == "FEE_DRAG"


class TestHardGatesImmutable:
    def test_low_confidence_is_warning_only_for_paper(self):
        paper_sig = {
            "side": "LONG", "last": 100.0, "confidence": 30,
            "expected_gross_edge_pct": 1.9,
            "profile": "PAPER_LEARNING",
            "entry_route": "PRICE_MOMENTUM_PROBE",
        }
        ok, reason, _ = dm.execution_quality_gate(
            _row(), paper_sig, dm.MODEL_CORE, CFG,
            profile="PAPER_LEARNING")
        assert ok and reason is None
        assert "LOW_CONFIDENCE" in paper_sig["quality_warnings"]

        strict_sig = dict(paper_sig)
        strict_sig.pop("quality_warnings")
        ok, reason, _ = dm.execution_quality_gate(
            _row(), strict_sig, dm.MODEL_CORE, CFG)
        assert not ok and reason == "LOW_CONFIDENCE"

    def test_net_rr_is_quality_warning_only_in_paper_learning(self):
        """Paper öğrenmede 1.20 R/R hedef, STRICT'te sert kapıdır."""
        kl = _klines_one_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        lsig["expected_gross_edge_pct"] = 1.9  # edge sorunu dışarıda
        ok, reason, _ = dm.execution_quality_gate(
            _row(), lsig, dm.MODEL_CORE, CFG, profile="PAPER_LEARNING")
        assert ok and reason is None
        assert lsig["quality_warnings"] == ["NET_REWARD_RISK_TOO_LOW"]

        strict_sig = dict(lsig)
        strict_sig.pop("quality_warnings")
        ok, reason, _ = dm.execution_quality_gate(
            _row(), strict_sig, dm.MODEL_CORE, CFG)
        assert not ok and reason == "NET_REWARD_RISK_TOO_LOW"

    def test_positive_net_below_cost_multiple_is_paper_warning(self):
        """ADR-014: Paper en geniş güvenli sınıra iner; komisyon ve
        kayma sonrası net hâlâ pozitifse 1.5x tampon yalnız etikettir.
        Aynı aday STRICT profilde sert ret kalır.
        """
        cfg = json.loads(json.dumps(CFG))
        cfg["core"]["tp_pct"] = 1.0  # R/R kapısını testten ayır
        paper_sig = {
            "side": "LONG", "last": 100.0, "confidence": 80,
            "expected_gross_edge_pct": 0.25,
            "profile": "PAPER_LEARNING",
            "entry_route": "EMA_OR_VWAP_CONFIRMATION",
        }

        ok, reason, net = dm.execution_quality_gate(
            _row(), paper_sig, dm.MODEL_CORE, cfg,
            profile="PAPER_LEARNING")
        assert ok and reason is None
        assert net > 0
        assert "EDGE_BELOW_COST_MULTIPLE" in \
            paper_sig["quality_warnings"]

        strict_sig = dict(paper_sig)
        strict_sig.pop("quality_warnings")
        ok, reason, strict_net = dm.execution_quality_gate(
            _row(), strict_sig, dm.MODEL_CORE, cfg)
        assert not ok and reason == "EDGE_BELOW_COST_MULTIPLE"
        assert strict_net == net

    def test_non_positive_net_remains_hard_in_paper_learning(self):
        paper_sig = {
            "side": "LONG", "last": 100.0, "confidence": 80,
            "expected_gross_edge_pct": 0.20,
            "profile": "PAPER_LEARNING",
            "entry_route": "EMA_OR_VWAP_CONFIRMATION",
        }
        ok, reason, net = dm.execution_quality_gate(
            _row(), paper_sig, dm.MODEL_CORE, CFG,
            profile="PAPER_LEARNING")
        assert not ok and reason == "FEE_DRAG"
        assert net < 0

    def test_liquidity_gate_still_applies(self):
        kl = _klines_one_block()
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        ok, reason, _ = dm.execution_quality_gate(
            _row(volume_usdt=1e4), lsig, dm.MODEL_CORE, CFG)
        assert not ok and reason == "LOW_LIQUIDITY"

    def test_natural_rising_low_volume_candidate_passes_paper_gate(self):
        sig, ok, reason, net = dm.evaluate_paper_learning_candidate(
            _row(), "XUSDT", _klines_rising_low_candle_volume(),
            dm.MODEL_OPP, CFG)

        assert ok and reason is None
        assert net > 0
        assert sig["side"] == "LONG"
        assert sig["entry_route"] == "STRICT_TREND"
        assert "MOMENTUM_EXHAUSTED" in sig["quality_warnings"]

    def test_natural_moderate_rise_passes_at_positive_net_boundary(self):
        sig, ok, reason, net = dm.evaluate_paper_learning_candidate(
            _row(), "XUSDT", _klines_moderate_rise(),
            dm.MODEL_CORE, CFG)

        assert ok and reason is None
        assert net > 0
        assert sig["expected_gross_edge_pct"] < 0.4
        assert "EDGE_BELOW_COST_MULTIPLE" in sig["quality_warnings"]


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

    def test_default_low_rr_candidate_completes_paper_flow(self):
        """ADR-010 uçtan uca kanıtı: varsayılan CORE net R/R değeri
        1.20 altında olsa da yalnız PAPER_LEARNING profilinde aday
        açılır, kapanır ve kalite uyarısı öğrenme günlüğüne taşınır.
        Aynı aday STRICT profilde reddedilmeye devam eder.
        """
        cfg = json.loads(json.dumps(CFG))
        kl = _klines_one_block()
        strict = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE)
        lsig = dm.evaluate_signal("XUSDT", kl, dm.MODEL_CORE,
                                  relax_ema_vwap=True)
        lsig["expected_gross_edge_pct"] = 1.9

        strict_ok, strict_reason, _ = dm.execution_quality_gate(
            _row(), dict(lsig), dm.MODEL_CORE, cfg)
        assert not strict_ok
        assert strict_reason == "NET_REWARD_RISK_TOO_LOW"

        ok, reason, net = dm.execution_quality_gate(
            _row(), lsig, dm.MODEL_CORE, cfg,
            profile="PAPER_LEARNING")
        assert ok and reason is None
        assert lsig["quality_warnings"] == [
            "NET_REWARD_RISK_TOO_LOW"]

        opened, why = dm.try_open_position(
            "XUSDT", dm.MODEL_CORE, lsig, net, cfg, now=1000.0)
        assert opened, why
        dm.record_learning_decision(
            "XUSDT", dm.MODEL_CORE, strict, lsig, True, None,
            net, cfg, opened, None)

        rt = json.load(open(dm.RUNTIME_PATH))
        position = rt["positions"]["XUSDT"]
        assert position["execution_mode"] == "PAPER"
        assert position["profile"] == "PAPER_LEARNING"
        assert rt["learning_journal"][0]["learning_decision"] == \
            "OPENED"
        assert rt["learning_journal"][0]["quality_warnings"] == [
            "NET_REWARD_RISK_TOO_LOW"]

        close_price = position["entry"] * (
            1 + cfg["core"]["tp_pct"] / 100)
        closed = dm.monitor_positions(lambda _symbol: close_price,
                                      cfg, now=1100.0)
        assert len(closed) == 1
        assert closed[0]["profile"] == "PAPER_LEARNING"
        assert closed[0]["execution_mode"] == "PAPER"
        assert closed[0]["quality_warnings"] == [
            "NET_REWARD_RISK_TOO_LOW"]

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
