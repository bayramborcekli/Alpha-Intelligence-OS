"""Task 57 — Risk geçmişi Spot geçişinde eski Futures kayıtlarını
yanlış yorumlamasın.

- Anlık görüntüler `universe: SPOT_ONLY` etiketiyle yazılır.
- _drawdown yalnızca aynı evrendeki (SPOT_ONLY) kayıtları karşılaştırır;
  Futures döneminden kalan etiketsiz/farklı kayıtlar hesaba katılmaz.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

import risk_api


@pytest.fixture
def hist(tmp_path, monkeypatch):
    p = tmp_path / "risk_history.jsonl"
    monkeypatch.setattr(risk_api, "HISTORY_PATH", p)
    return p


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _write(p, records):
    import json
    with p.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


class TestDrawdownUniverse:
    def test_mixed_history_ignores_futures_records(self, hist):
        # Eski Futures kaydı: universe etiketi yok, çok büyük marj bakiyesi
        _write(hist, [
            {"date": _today(), "margin_balance_usdt": "100000"},
            {"date": _today(), "universe": "SPOT_ONLY",
             "margin_balance_usdt": "100"},
        ])
        dd = risk_api._drawdown(1, Decimal("90"))
        # Zirve 100 (SPOT_ONLY) olmalı, 100000 (Futures) DEĞİL
        assert dd == "-10.00"

    def test_only_futures_records_returns_null(self, hist):
        _write(hist, [
            {"date": _today(), "margin_balance_usdt": "100000"},
            {"date": _today(), "universe": "FUTURES",
             "margin_balance_usdt": "50000"},
        ])
        # Yalnız evren-dışı kayıt var → doğrulanmış Spot geçmişi yok → null
        assert risk_api._drawdown(1, Decimal("90")) is None

    def test_untagged_spot_era_record_recognized(self, hist):
        # Etiket öncesi Spot dönemi kaydı: universe yok ama
        # total_spot_value_usdt alanı var → Spot sayılır
        _write(hist, [
            {"date": _today(), "margin_balance_usdt": "200",
             "total_spot_value_usdt": "200"},
        ])
        assert risk_api._drawdown(1, Decimal("100")) == "-50.00"

    def test_no_history_returns_null(self, hist):
        # Hiç geçmiş yok → null (tahmin üretilmez)
        assert risk_api._drawdown(1, Decimal("100")) is None

    def test_null_balance_returns_none(self, hist):
        assert risk_api._drawdown(1, None) is None


class TestSnapshotTag:
    def test_append_snapshot_carries_universe_tag(self, hist, monkeypatch):
        snap = {"date": _today(), "universe": "SPOT_ONLY",
                "margin_balance_usdt": "123.45"}
        risk_api._append_snapshot(snap)
        recs = risk_api._read_history()
        assert len(recs) == 1
        assert recs[0]["universe"] == "SPOT_ONLY"

    def test_summary_snapshot_includes_universe(self):
        # summary() içindeki snapshot sözlüğü kaynak kodda etiketi taşımalı
        import inspect
        src = inspect.getsource(risk_api.summary)
        assert '"universe": "SPOT_ONLY"' in src
