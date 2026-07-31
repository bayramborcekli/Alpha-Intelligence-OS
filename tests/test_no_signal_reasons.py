"""NEDEN_NO_SIGNAL — NO_SIGNAL alt neden üretimi ve istatistiği.

Sözleşme:
- evaluate_signal NO_SIGNAL dönerken sub_reason (EMA/VWAP bloğu) ve
  weak_factors (teşhis) üretir; giriş kararı DEĞİŞMEZ.
- record_rejection detail'i kayda taşır; bilinmeyen alt neden atılır.
- rejection_breakdown reason + alt neden dağılımını yüzdeyle verir.
- symbol_status alt nedeni kanonik haritaya taşır.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402


def _klines(closes, vols=None):
    vols = vols or [100.0] * len(closes)
    return [[0, c, c, c, c, v] for c, v in zip(closes, vols)]


def _downtrend():
    # Düşen seri: EMA9 < EMA21 ve fiyat VWAP altında
    closes = [100 - i * 0.5 for i in range(40)]
    return _klines(closes)


def _uptrend_below_vwap():
    # EMA9 > EMA21 ama son fiyat pencere VWAP'ının altında:
    # uzun yükseliş sonrası sert ama trendi bozmayan geri çekilme
    closes = [100 + i for i in range(35)] + [128.0, 126.0, 124.0,
                                             122.0, 121.0]
    return _klines(closes)


class TestEvaluateSignalSubReason:
    def test_downtrend_emits_ema_vwap_block(self):
        sig = dm.evaluate_signal("XUSDT", _downtrend(), dm.MODEL_CORE)
        assert sig["side"] is None
        assert sig["reason_code"] == "NO_SIGNAL"
        assert sig["sub_reason"] in dm.NO_SIGNAL_SUB_REASONS
        assert sig["sub_reason"] == "EMA_VWAP_BLOCK"
        assert isinstance(sig["weak_factors"], list)

    def test_vwap_block_when_only_price_below_vwap(self):
        sig = dm.evaluate_signal("XUSDT", _uptrend_below_vwap(),
                                 dm.MODEL_CORE)
        if sig.get("reason_code") == "NO_SIGNAL":
            assert sig["sub_reason"] in ("VWAP_BLOCK",
                                         "EMA_VWAP_BLOCK")

    def test_data_quality_has_no_sub_reason(self):
        sig = dm.evaluate_signal("XUSDT", _klines([1.0] * 5),
                                 dm.MODEL_CORE)
        assert sig["reason_code"] == "DATA_QUALITY"
        assert "sub_reason" not in sig

    def test_entry_behavior_unchanged(self):
        # Alt neden alanları yalnız NO_SIGNAL dönüşüne eklenir;
        # kabul yolunda side/confidence yapısı aynı kalır.
        closes = [100.0] * 20 + [100 + i * 0.4 for i in range(15)]
        vols = [100.0] * 30 + [400.0] * 5
        sig = dm.evaluate_signal("XUSDT", _klines(closes, vols),
                                 dm.MODEL_CORE)
        if sig.get("side"):
            assert "sub_reason" not in sig


class TestRecordRejectionDetail:
    def test_detail_persisted(self, tmp_path, monkeypatch):
        rt_file = tmp_path / "rt.json"
        state = {}

        def fake_update(mut):
            mut(state)

        def fake_load():
            return state

        monkeypatch.setattr(dm, "_update_runtime", fake_update)
        monkeypatch.setattr(dm, "_load_runtime", fake_load)
        dm.record_rejection("AUSDT", dm.MODEL_CORE, "NO_SIGNAL",
                            detail={"sub_reason": "EMA_BLOCK",
                                    "weak_factors": ["VOLUME_WEAK"]})
        rej = state["rejections"][0]
        assert rej["sub_reason"] == "EMA_BLOCK"
        assert rej["weak_factors"] == ["VOLUME_WEAK"]
        assert rt_file.exists() is False  # dosyaya değil, mut'a yazdık

    def test_unknown_sub_reason_dropped(self, monkeypatch):
        state = {}
        monkeypatch.setattr(dm, "_update_runtime",
                            lambda mut: mut(state))
        dm.record_rejection("BUSDT", dm.MODEL_OPP, "NO_SIGNAL",
                            detail={"sub_reason": "HACKED",
                                    "weak_factors": []})
        assert "sub_reason" not in state["rejections"][0]

    def test_no_detail_backward_compatible(self, monkeypatch):
        state = {}
        monkeypatch.setattr(dm, "_update_runtime",
                            lambda mut: mut(state))
        dm.record_rejection("CUSDT", dm.MODEL_CORE, "FEE_DRAG")
        rej = state["rejections"][0]
        assert rej["reason_code"] == "FEE_DRAG"
        assert "sub_reason" not in rej


class TestRejectionBreakdown:
    RT = {"rejections": [
        {"symbol": "A", "model": dm.MODEL_CORE,
         "reason_code": "NO_SIGNAL", "sub_reason": "EMA_VWAP_BLOCK",
         "weak_factors": ["VOLUME_WEAK", "MOMENTUM_WEAK"],
         "at": "2026-07-31T06:00:00+00:00"},
        {"symbol": "B", "model": dm.MODEL_CORE,
         "reason_code": "NO_SIGNAL", "sub_reason": "EMA_BLOCK",
         "weak_factors": ["VOLUME_WEAK"],
         "at": "2026-07-31T06:00:01+00:00"},
        {"symbol": "C", "model": dm.MODEL_OPP,
         "reason_code": "FEE_DRAG",
         "at": "2026-07-31T06:00:02+00:00"},
        {"symbol": "D", "model": dm.MODEL_OPP,
         "reason_code": "NO_SIGNAL",  # eski kayıt — alt neden yok
         "at": "2026-07-31T06:00:03+00:00"},
    ]}

    def test_counts_and_percentages(self):
        bd = dm.rejection_breakdown(self.RT)
        assert bd["sample_size"] == 4
        assert bd["reasons"]["NO_SIGNAL"]["count"] == 3
        assert bd["reasons"]["NO_SIGNAL"]["pct"] == 75.0
        ns = bd["no_signal"]
        assert ns["total"] == 3
        assert ns["sub_reason_tagged"] == 2
        assert ns["sub_reason_untagged"] == 1  # eski kayıt dürüstçe
        assert ns["sub_reasons"]["EMA_VWAP_BLOCK"]["pct"] == 50.0
        assert ns["weak_factors"]["VOLUME_WEAK"]["count"] == 2

    def test_empty_runtime_safe(self):
        bd = dm.rejection_breakdown({})
        assert bd["sample_size"] == 0
        assert bd["no_signal"]["total"] == 0

    def test_in_panel_state_contract(self, monkeypatch):
        monkeypatch.setattr(dm, "_load_runtime", lambda: dict(self.RT))
        bd = dm.rejection_breakdown()
        assert bd["scope"] == "runtime_rejections_last_300_max"


class TestSymbolStatusSubReason:
    def test_sub_reason_carried(self, monkeypatch):
        rt = {"core_list": [{"symbol": "AUSDT"}],
              "opportunity_list": [],
              "rejections": [{"symbol": "AUSDT",
                              "model": dm.MODEL_CORE,
                              "reason_code": "NO_SIGNAL",
                              "sub_reason": "VWAP_BLOCK",
                              "weak_factors": ["RSI_OUT_OF_BAND"],
                              "at": "2026-07-31T06:00:00+00:00"}],
              "positions": {}, "last_refresh": "x"}
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        ss = dm.symbol_status()
        st = ss["symbols"]["AUSDT"]
        assert st["last_rejection_reason"] == "NO_SIGNAL"
        assert st["last_rejection_sub_reason"] == "VWAP_BLOCK"
        assert st["weak_factors"] == ["RSI_OUT_OF_BAND"]

    def test_legacy_rejection_without_sub(self, monkeypatch):
        rt = {"core_list": [], "opportunity_list": [],
              "rejections": [{"symbol": "BUSDT",
                              "model": dm.MODEL_OPP,
                              "reason_code": "NO_SIGNAL",
                              "at": "2026-07-31T06:00:00+00:00"}],
              "positions": {}, "last_refresh": "x"}
        monkeypatch.setattr(dm, "_load_runtime", lambda: rt)
        st = dm.symbol_status()["symbols"]["BUSDT"]
        assert st["last_rejection_sub_reason"] is None
