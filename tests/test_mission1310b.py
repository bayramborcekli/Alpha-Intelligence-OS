"""Mission 1310B ledger mantığı testleri (ağ erişimi yok)."""
from decimal import Decimal

from tools.mission1310b import (
    ingest_events,
    normalize_movement,
    payload_hash,
    reconcile,
    reconstruct_totals,
    stable_event_id,
)

DEP = {"id": 1, "asset": "USDT", "amount": "100.5", "txId": "0xabc",
       "insertTime": "1666589730000", "transferType": 0, "status": 1,
       "network": "BSC"}
WD = {"id": 2, "asset": "USDT", "amount": "40", "fee": "0.29",
      "txId": "0xdef", "createTime": 1666600000000, "transferType": 0,
      "status": 10, "network": "BSC"}


def test_stable_event_id_deterministic():
    a = stable_event_id("BINANCE_TR", "0xabc", "USDT", "100.5", 1)
    b = stable_event_id("BINANCE_TR", "0xabc", "USDT", "100.5", 1)
    assert a == b and a.startswith("EVT-")
    assert a != stable_event_id("BINANCE_TR", "0xabc", "USDT", "100.6", 1)


def test_normalize_classification():
    d = normalize_movement(DEP, "deposit")
    w = normalize_movement(WD, "withdrawal")
    assert d["class"] == "DEPOSIT" and w["class"] == "WITHDRAWAL"
    assert d["timestamp_ms"] == 1666589730000
    internal = normalize_movement({**DEP, "transferType": 1}, "deposit")
    assert internal["class"] == "INTERNAL_TRANSFER"


def test_normalize_malformed_is_unknown():
    assert normalize_movement({"asset": "USDT"}, "deposit")["class"] == "UNKNOWN"
    bad_amt = normalize_movement({**DEP, "amount": "abc"}, "deposit")
    assert bad_amt["class"] == "UNKNOWN"
    no_ts = normalize_movement({**DEP, "insertTime": ""}, "deposit")
    assert no_ts["class"] == "UNKNOWN"


def test_payload_hash_has_no_secret_material():
    h = payload_hash(DEP)
    assert len(h) == 64 and h == payload_hash(dict(DEP))


def test_ingest_blocks_duplicates_and_preserves_history():
    d = normalize_movement(DEP, "deposit")
    w = normalize_movement(WD, "withdrawal")
    merged, added, dupes = ingest_events([], [d, w])
    assert (added, dupes) == (2, 0)
    # Aynı olaylar ikinci kez → tamamı bloklanır, geçmiş aynen korunur
    merged2, added2, dupes2 = ingest_events(merged, [d, w])
    assert (added2, dupes2) == (0, 2)
    assert merged2 == merged


def test_ingest_ordering_by_timestamp():
    d = normalize_movement(DEP, "deposit")           # t=1666589730000
    w = normalize_movement(WD, "withdrawal")         # t=1666600000000
    merged, _, _ = ingest_events([], [w, d])
    assert [e["event_id"] for e in merged] == [d["event_id"], w["event_id"]]


def test_reconstruct_and_reconcile():
    d = normalize_movement(DEP, "deposit")
    w = normalize_movement(WD, "withdrawal")
    totals = reconstruct_totals([d, w])
    assert totals["USDT"] == Decimal("100.5") - Decimal("40.29")
    # Tam eşleşme → fark yok
    assert reconcile({"USDT": totals["USDT"]}, totals) == {}
    # Uyuşmazlık → açıklanamayan fark raporlanır
    diffs = reconcile({"USDT": Decimal("70")}, totals)
    assert diffs["USDT"] == Decimal("70") - totals["USDT"]


def test_unknown_events_excluded_from_totals():
    u = normalize_movement({"asset": "USDT"}, "deposit")
    assert reconstruct_totals([u]) == {}
