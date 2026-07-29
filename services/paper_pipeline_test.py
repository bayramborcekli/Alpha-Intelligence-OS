"""PAPER PIPELINE TEST — sentetik, TEST etiketli hat doğrulaması.

Gerçek piyasa sinyali beklemeden şu hattı uçtan uca doğrular:

sentetik sinyal → decision_engine.score_decision → seçili risk
profili → adaptive_risk.calculate_risk + calculate_position_size
(GERÇEK motorlar) → Paper intent (TEST) → test ledger

Kurallar:
- gerçek Binance emri GÖNDERMEZ (hiçbir ağ çağrısı yok)
- normal performans istatistiklerine KARIŞMAZ (ayrı test ledger
  dosyası: alpha20_v1/pipeline_test_ledger.jsonl, git dışı)
- gerçek risk eşiklerini değiştirmez (salt-okunur kullanım)
- her koşu audit kaydı üretir; kayıt açıkça TEST etiketlidir
- varsayılan kapalı: yalnız açık onay ('PIPELINE TEST') ile çalışır
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ALPHA_DIR = ROOT / "alpha20_v1"
TEST_LEDGER = ALPHA_DIR / "pipeline_test_ledger.jsonl"

CONFIRMATION = "PIPELINE TEST"


def run(confirmation: str, actor: str) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        return {"ok": False, "error": "CONFIRMATION_REQUIRED",
                "message": f"Onay ifadesi gerekli: '{CONFIRMATION}'"}
    p = str(ALPHA_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)
    import adaptive_risk as ar
    import decision_engine as de
    from services import risk_profiles as rp

    correlation_id = f"ppt-{uuid.uuid4().hex[:12]}"
    symbol = "TESTUSDT"

    # 1) Sentetik sinyal/feature girdileri (açıkça TEST)
    synthetic = {
        "strategy_score": 82.0, "regime_score": 75.0,
        "regime_confidence": 80.0, "coin_score": 80.0,
        "volume_24h_usdt": 5e8, "atr_pct": 2.0,
        "regime": "Yükseliş", "paper_hist_score": 60.0,
        "data_quality_score": 95.0,
    }
    final_score, category, components, reason = de.score_decision(
        strategy_score=synthetic["strategy_score"],
        regime_score=synthetic["regime_score"],
        regime_confidence=synthetic["regime_confidence"],
        coin_score=synthetic["coin_score"],
        volume_24h_usdt=synthetic["volume_24h_usdt"],
        atr_pct=synthetic["atr_pct"],
        regime=synthetic["regime"],
        paper_hist_score=synthetic["paper_hist_score"],
        data_quality_score=synthetic["data_quality_score"],
    )

    # 2) Seçili profil → GERÇEK adaptive_risk
    profile = rp.current_profile()
    adaptive_cfg = {"enabled": True, "mode": "MONITOR",
                    **rp.adaptive_flags(profile["name"])}
    trading_state = {"balance": 10_000.0, "trades": [],
                     "position": None}
    risk_res = ar.calculate_risk(
        trading_state=trading_state, adaptive_cfg=adaptive_cfg,
        regime_info={"regime": synthetic["regime"],
                     "confidence": synthetic["regime_confidence"],
                     "atr_pct": synthetic["atr_pct"]},
        final_decision_score=final_score,
        data_quality_score=synthetic["data_quality_score"])
    entry, atr = 100.0, 2.0
    qty, stop_dist, err = ar.calculate_position_size(
        balance=trading_state["balance"],
        risk_pct=risk_res.risk_pct, entry=entry, stop=0,
        atr=atr, atr_stop_multiplier=1.5,
        adaptive_cfg=adaptive_cfg)

    # 3) TEST Paper intent + test ledger (gerçek ledger'a yazılmaz)
    record = {
        "TEST": True,
        "label": "PAPER_PIPELINE_TEST",
        "correlation_id": correlation_id,
        "actor": actor,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "synthetic_inputs": synthetic,
        "decision_score": round(final_score, 2),
        "category": category,
        "components": components,
        "reason": reason,
        **rp.decision_fields(profile["name"]),
        "risk_result": {"allowed": risk_res.allowed,
                        "risk_pct": risk_res.risk_pct,
                        "reason": risk_res.reason},
        "calculated_position_size": qty,
        "stop_distance": stop_dist,
        "sizing_error": err or None,
        "paper_intent": {
            "type": "TEST_INTENT", "symbol": symbol,
            "side": "BUY", "qty": qty, "entry": entry,
            "live_order": False},
        "live_orders": "DISABLED",
    }
    TEST_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with TEST_LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return {"ok": True, "result": record}


def clear() -> int:
    """Test kayıtlarını temizle; silinen kayıt sayısını döndür."""
    if not TEST_LEDGER.exists():
        return 0
    n = sum(1 for line in
            TEST_LEDGER.read_text(encoding="utf-8").splitlines()
            if line.strip())
    TEST_LEDGER.unlink()
    return n
