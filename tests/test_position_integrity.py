"""Yetim / eksik aktif pozisyon koruması (ONDOUSDT vakası).

Güvenceler:
1. Restart sonrası sağlıklı pozisyon eksiksiz hydrate edilir (OPEN).
2. Yetim (trades'te kapanışı olan) kayıt aktif listede görünmez ve
   ORPHAN_POSITION audit kaydı yazılır.
3. Eksik veriyle pozisyon 'Yönetiliyor' (OPEN/ACTIVE) gösterilmez —
   INCOMPLETE_POSITION_DATA / STALE_POSITION dürüst durum kodları.
4. Eksik fiyat/miktarla otomatik veya manuel exit YAPILMAZ.
5. LIVE ORDERS DISABLED korunur.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import dual_model as dm  # noqa: E402


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) -
            timedelta(hours=hours_ago)).isoformat()


@pytest.fixture()
def appmod(tmp_path, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, "POSITION_AUDIT_PATH",
                        tmp_path / "audit.jsonl")
    return appmod


# ── Legacy state.json pozisyon sınıflandırması ─────────────────────

class TestLegacyClassification:
    def test_healthy_position_hydrates_open(self, appmod):
        pos = {"symbol": "ONDOUSDT", "side": "LONG", "entry": 0.95,
               "quantity": 100.0, "opened_at": _iso(0.5)}
        c = appmod._classify_legacy_position(pos, {"trades": []})
        assert c == {"status": "OPEN", "entry": 0.95,
                     "quantity": 100.0}

    def test_alternate_keys_hydrate(self, appmod):
        # entry/quantity yoksa entry_price/qty'den hydrate edilir.
        pos = {"symbol": "ONDOUSDT", "entry_price": 0.95, "qty": 100,
               "opened_at": _iso(0.5)}
        c = appmod._classify_legacy_position(pos, {})
        assert c["status"] == "OPEN"
        assert c["entry"] == 0.95 and c["quantity"] == 100.0

    def test_missing_fields_incomplete_not_managed(self, appmod):
        pos = {"symbol": "ONDOUSDT", "side": "LONG",
               "opened_at": _iso(1)}
        c = appmod._classify_legacy_position(pos, {})
        assert c["status"] == "INCOMPLETE_POSITION_DATA"
        audit = appmod.POSITION_AUDIT_PATH.read_text(encoding="utf-8")
        rec = json.loads(audit.strip().splitlines()[-1])
        assert rec["symbol"] == "ONDOUSDT"
        assert rec["reason"] == "INCOMPLETE_POSITION_DATA"

    def test_orphan_when_trade_closed_after_open(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(5)}
        state = {"trades": [{"symbol": "ONDOUSDT",
                             "closed_at": _iso(4)}]}
        c = appmod._classify_legacy_position(pos, state)
        assert c["status"] == "ORPHAN_POSITION"
        rec = json.loads(appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()[-1])
        assert rec["reason"] == "ORPHAN_POSITION"

    def test_orphan_epoch_timestamp_normalized(self, appmod):
        # trades 'time' alanı epoch gelse bile kıyas normalize
        # datetime üzerinden yapılır (string kıyası yok).
        opened = datetime.now(timezone.utc) - timedelta(hours=5)
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": opened.isoformat()}
        state = {"trades": [{
            "symbol": "ONDOUSDT",
            "time": (opened + timedelta(hours=1)).timestamp()}]}
        c = appmod._classify_legacy_position(pos, state)
        assert c["status"] == "ORPHAN_POSITION"

    def test_unparseable_trade_ts_is_not_orphan_proof(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(1)}
        state = {"trades": [{"symbol": "ONDOUSDT",
                             "closed_at": "zzz-bozuk"}]}
        c = appmod._classify_legacy_position(pos, state)
        assert c["status"] == "OPEN"  # yanlış pozitif ORPHAN yok

    def test_stale_threshold_configurable(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(2)}
        c = appmod._classify_legacy_position(pos, {}, stale_hours=1.0)
        assert c["status"] == "STALE_POSITION"
        c2 = appmod._classify_legacy_position(pos, {},
                                              stale_hours=10.0)
        assert c2["status"] == "OPEN"

    def test_default_stale_threshold_not_aggressive(self, appmod):
        # Legacy motorda max-hold yok — 5 saatlik sağlıklı pozisyon
        # varsayılan eşikte bayat sayılmaz (mimar bulgusu).
        assert appmod.LEGACY_POSITION_STALE_HOURS >= 24
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(5)}
        assert appmod._classify_legacy_position(
            pos, {"trades": []})["status"] == "OPEN"

    def test_stale_after_threshold(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": _iso(
                   appmod.LEGACY_POSITION_STALE_HOURS + 1.5)}
        c = appmod._classify_legacy_position(pos, {"trades": []})
        assert c["status"] == "STALE_POSITION"

    def test_bad_opened_at_incomplete(self, appmod):
        pos = {"symbol": "ONDOUSDT", "entry": 0.95, "quantity": 10,
               "opened_at": "bozuk-tarih"}
        c = appmod._classify_legacy_position(pos, {})
        assert c["status"] == "INCOMPLETE_POSITION_DATA"

    def test_audit_dedupes_consecutive(self, appmod):
        pos = {"symbol": "ONDOUSDT", "opened_at": _iso(1)}
        appmod._classify_legacy_position(pos, {})
        appmod._classify_legacy_position(pos, {})
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_audit_no_dedupe_across_sources(self, appmod):
        # Task 153: symbol+reason aynı olsa da source farklıysa
        # ikinci kayıt yutulmaz (legacy vs dual-model operator_ack).
        appmod._audit_position_integrity(
            "ONDOUSDT", "operator_ack", "legacy ack",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "ONDOUSDT", "operator_ack", "dual ack",
            source="dual_model_position")
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        recs = [json.loads(x) for x in lines]
        assert {r["source"] for r in recs} == {
            "legacy_state_position", "dual_model_position"}

    def test_audit_dedupes_same_source(self, appmod):
        # Aynı symbol+reason+source ardışık gelirse hâlâ tek kayıt.
        appmod._audit_position_integrity(
            "ONDOUSDT", "operator_ack", "a",
            source="dual_model_position")
        appmod._audit_position_integrity(
            "ONDOUSDT", "operator_ack", "b",
            source="dual_model_position")
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_audit_dedupes_alternating_sources(self, appmod):
        # Task 156: legacy/dual dönüşümlü aynı INCOMPLETE'i yazarsa
        # her kaynaktan yalnız İLK kayıt tutulur — dosya şişmez.
        for i in range(6):
            src = ("legacy_state_position" if i % 2 == 0
                   else "dual_model_position")
            appmod._audit_position_integrity(
                "ONDOUSDT", "INCOMPLETE_POSITION_DATA", f"d{i}",
                source=src)
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        recs = [json.loads(x) for x in lines]
        assert {r["source"] for r in recs} == {
            "legacy_state_position", "dual_model_position"}
        # İLK yazımlar korunur (durum değişikliği bilgisi kaybolmaz).
        assert [r["detail"] for r in recs] == ["d0", "d1"]

    def test_audit_streak_break_allows_rewrite(self, appmod):
        # Farklı reason araya girince seri kırılır: aynı
        # symbol+reason+source yeniden yazılabilir.
        appmod._audit_position_integrity(
            "ONDOUSDT", "INCOMPLETE_POSITION_DATA", "a",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "ONDOUSDT", "ORPHAN_POSITION", "b",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "ONDOUSDT", "INCOMPLETE_POSITION_DATA", "c",
            source="legacy_state_position")
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_audit_other_symbol_does_not_break_streak(self, appmod):
        appmod._audit_position_integrity(
            "ONDOUSDT", "INCOMPLETE_POSITION_DATA", "a",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "YUSDT", "ORPHAN_POSITION", "b",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "ONDOUSDT", "INCOMPLETE_POSITION_DATA", "c",
            source="legacy_state_position")
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # üçüncü yazım yutuldu

    # ── Task 157: çok sembollü dönüşümlü yazım tail'i taşırmasın ────

    def test_audit_many_symbols_interleaved_no_bloat(self, appmod):
        # 80 sembol dönüşümlü olarak aynı durumu tekrar tekrar
        # yazar; toplam hacim eski 4KB tail penceresini kat kat
        # aşar. Dedupe yine de kaçmaz: sembol başına tek kayıt.
        syms = [f"S{i:03d}USDT" for i in range(80)]
        for _round in range(4):
            for s in syms:
                appmod._audit_position_integrity(
                    s, "INCOMPLETE_POSITION_DATA", "x" * 120,
                    source="legacy_state_position")
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == len(syms)
        recs = [json.loads(x) for x in lines]
        assert {r["symbol"] for r in recs} == set(syms)

    def test_audit_dedupe_survives_cache_loss(self, appmod):
        # Worker restart / başka worker senaryosu: in-memory önbellek
        # boşaltılsa bile büyütülmüş tail penceresi (>=64KB) çok
        # sembollü dönüşümlü yazımda dedupe'u dosyadan yakalar.
        assert appmod._AUDIT_TAIL_BYTES >= 65536
        syms = [f"W{i:03d}USDT" for i in range(60)]
        for _round in range(3):
            for s in syms:
                appmod._audit_position_integrity(
                    s, "INCOMPLETE_POSITION_DATA", "y" * 120,
                    source="dual_model_position")
            appmod._AUDIT_DEDUP_CACHE.clear()  # önbellek kaybı
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == len(syms)

    def test_audit_stale_cache_external_streak_break(self, appmod):
        # Başka worker'ın araya farklı reason yazması (seri kırılması)
        # bu worker'ın bayat önbelleğiyle YUTULMAZ: dosya boyutu
        # değiştiği için kısa devre düşer, tail taraması yeniden
        # yazıma izin verir.
        appmod._audit_position_integrity(
            "EXTUSDT", "INCOMPLETE_POSITION_DATA", "a",
            source="legacy_state_position")
        # Dışarıdan (başka worker) seri kıran kayıt eklenir.
        with appmod.POSITION_AUDIT_PATH.open(
                "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": _iso(0), "symbol": "EXTUSDT",
                "reason": "ORPHAN_POSITION", "detail": "ext",
                "source": "legacy_state_position"}) + "\n")
        appmod._audit_position_integrity(
            "EXTUSDT", "INCOMPLETE_POSITION_DATA", "c",
            source="legacy_state_position")
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[-1])["detail"] == "c"

    def test_audit_cache_shortcircuit_only_when_file_unchanged(
            self, appmod):
        # Dosya değişmemişken tekrar → kısa devre yutar; başka
        # sembolün araya yazması dosyayı değiştirir ama tail taraması
        # yine dup bulur — dosya şişmez.
        appmod._audit_position_integrity(
            "FRUSDT", "STALE_POSITION", "a",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "FRUSDT", "STALE_POSITION", "b",
            source="legacy_state_position")  # kısa devre
        appmod._audit_position_integrity(
            "OTHUSDT", "STALE_POSITION", "o",
            source="legacy_state_position")  # dosyayı değiştirir
        appmod._audit_position_integrity(
            "FRUSDT", "STALE_POSITION", "c",
            source="legacy_state_position")  # tail dup yakalar
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_audit_cache_respects_streak_break(self, appmod):
        # Önbellek seri-kırılma semantiğini bozmaz: reason değişince
        # aynı symbol+reason+source yeniden yazılabilir.
        appmod._audit_position_integrity(
            "CBUSDT", "INCOMPLETE_POSITION_DATA", "a",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "CBUSDT", "ORPHAN_POSITION", "b",
            source="legacy_state_position")
        appmod._audit_position_integrity(
            "CBUSDT", "INCOMPLETE_POSITION_DATA", "c",
            source="legacy_state_position")
        lines = appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_orphan_excluded_from_overview_source(self):
        # _operation_raw ORPHAN'ı aktif listeye almaz (kaynak kodu
        # sözleşmesi — davranış birim testleri yukarıda).
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        assert '_cls["status"] != "ORPHAN_POSITION"' in src
        assert '"position_status": _cls["status"]' in src
        assert '"position_status": "OPEN"' not in src.split(
            "_cls = _classify_legacy_position(")[1][:2000]


# ── Task 144: panel banner uyarıları ───────────────────────────────

class TestIntegrityPanelWarnings:
    @pytest.fixture()
    def panelmod(self, tmp_path, monkeypatch, appmod):
        monkeypatch.setattr(appmod, "STATE_PATH",
                            tmp_path / "state.json")
        monkeypatch.setattr(appmod, "load_config",
                            lambda: ({}, None))
        return appmod

    def _write_state(self, appmod, state: dict):
        appmod.STATE_PATH.write_text(
            json.dumps(state), encoding="utf-8")

    def test_orphan_detection_produces_warning(self, panelmod):
        self._write_state(panelmod, {
            "position": {"symbol": "ONDOUSDT", "entry": 0.95,
                         "quantity": 10, "opened_at": _iso(5)},
            "trades": [{"symbol": "ONDOUSDT", "closed_at": _iso(4)}],
        })
        panel = panelmod._position_integrity_panel()
        assert len(panel["warnings"]) == 1
        assert "Yetim pozisyon kaydı" in panel["warnings"][0]
        assert "ONDOUSDT" in panel["warnings"][0]
        # Son audit kaydı da API'dan okunabilir.
        assert panel["recent_audit"][0]["reason"] == "ORPHAN_POSITION"

    def test_incomplete_and_stale_labels(self, panelmod):
        self._write_state(panelmod, {
            "position": {"symbol": "XUSDT", "opened_at": _iso(1)},
            "trades": [],
        })
        panel = panelmod._position_integrity_panel()
        assert "Eksik pozisyon verisi" in panel["warnings"][0]

    def test_warning_clears_when_state_cleaned(self, panelmod):
        self._write_state(panelmod, {
            "position": {"symbol": "ONDOUSDT", "entry": 0.95,
                         "quantity": 10, "opened_at": _iso(5)},
            "trades": [{"symbol": "ONDOUSDT", "closed_at": _iso(4)}],
        })
        assert panelmod._position_integrity_panel()["warnings"]
        # State temizlenince uyarı kendiliğinden kaybolur; audit
        # geçmişi kaybolmaz.
        self._write_state(panelmod, {"position": None, "trades": []})
        panel = panelmod._position_integrity_panel()
        assert panel["warnings"] == []
        assert panel["recent_audit"]  # geçmiş hâlâ okunabilir

    # ── Task 150: uzun süre çözümsüz INCOMPLETE → KRİTİK uyarı ─────

    def _write_audit(self, appmod, recs):
        with appmod.POSITION_AUDIT_PATH.open(
                "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

    def _incomplete_state(self, appmod):
        self._write_state(appmod, {
            "position": {"symbol": "XUSDT", "opened_at": _iso(1)},
            "trades": [],
        })

    def test_incomplete_escalates_after_threshold(self, panelmod):
        self._incomplete_state(panelmod)
        self._write_audit(panelmod, [{
            "ts": _iso(6), "symbol": "XUSDT",
            "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"}])
        panel = panelmod._position_integrity_panel()
        assert len(panel["warnings"]) == 1
        w = panel["warnings"][0]
        assert w.startswith("KRİTİK:")
        assert "saattir çözümsüz" in w and "XUSDT" in w

    def test_incomplete_fresh_not_escalated(self, panelmod):
        self._incomplete_state(panelmod)
        self._write_audit(panelmod, [{
            "ts": _iso(1), "symbol": "XUSDT",
            "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"}])
        w = panelmod._position_integrity_panel()["warnings"][0]
        assert not w.startswith("KRİTİK:")
        assert "Eksik pozisyon verisi" in w

    def test_incomplete_threshold_configurable(self, panelmod,
                                               monkeypatch):
        # position_stale_hours yükseltme eşiğini de belirler.
        monkeypatch.setattr(panelmod, "load_config",
                            lambda: ({"position_stale_hours": 10}, None))
        self._incomplete_state(panelmod)
        self._write_audit(panelmod, [{
            "ts": _iso(6), "symbol": "XUSDT",
            "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"}])
        w = panelmod._position_integrity_panel()["warnings"][0]
        assert not w.startswith("KRİTİK:")  # 6h < 10h eşiği

    def test_broken_streak_not_escalated(self, panelmod):
        # Sembolün EN YENİ audit kaydı farklı durumdaysa seri
        # kırılmıştır — eski INCOMPLETE kaydına dayanıp yükseltilmez.
        self._incomplete_state(panelmod)
        self._write_audit(panelmod, [
            {"ts": _iso(9), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"},
            {"ts": _iso(8), "symbol": "XUSDT",
             "reason": "ORPHAN_POSITION", "detail": "d"},
        ])
        w = panelmod._position_integrity_panel()["warnings"][0]
        assert not w.startswith("KRİTİK:")

    def test_incomplete_since_reads_streak_start(self, panelmod):
        ts = _iso(7)
        self._write_audit(panelmod, [
            {"ts": _iso(9), "symbol": "YUSDT",
             "reason": "STALE_POSITION", "detail": "d"},
            {"ts": ts, "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"},
        ])
        since, exact = panelmod._incomplete_since("XUSDT")
        assert since is not None and since.isoformat() == ts
        assert exact
        assert panelmod._incomplete_since("YUSDT")[0] is None
        assert panelmod._incomplete_since("ZUSDT")[0] is None

    def test_incomplete_since_alternating_sources_oldest(
            self, panelmod):
        # Task 154: legacy/dual kaynakları dönüşümlü INCOMPLETE
        # yazarsa seri başlangıcı EN ESKİ kayıttır — sayaç sıfırlanmaz.
        oldest = _iso(7)
        self._write_audit(panelmod, [
            {"ts": oldest, "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "legacy_state_position"},
            {"ts": _iso(5), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "dual_model_position"},
            {"ts": _iso(3), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "legacy_state_position"},
        ])
        since, exact = panelmod._incomplete_since("XUSDT")
        assert since is not None and since.isoformat() == oldest
        assert exact

    def test_incomplete_since_streak_broken_by_other_reason(
            self, panelmod):
        # Araya giren farklı durum kaydı seriyi kırar: yalnız
        # kesintiden SONRAKİ INCOMPLETE kayıtları sayılır.
        restart = _iso(4)
        self._write_audit(panelmod, [
            {"ts": _iso(9), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"},
            {"ts": _iso(6), "symbol": "XUSDT",
             "reason": "ORPHAN_POSITION", "detail": "d"},
            {"ts": restart, "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "dual_model_position"},
            {"ts": _iso(2), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "legacy_state_position"},
        ])
        since, exact = panelmod._incomplete_since("XUSDT")
        assert since is not None and since.isoformat() == restart
        assert exact

    def test_incomplete_since_other_symbols_do_not_break(
            self, panelmod):
        oldest = _iso(8)
        self._write_audit(panelmod, [
            {"ts": oldest, "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"},
            {"ts": _iso(6), "symbol": "YUSDT",
             "reason": "ORPHAN_POSITION", "detail": "d"},
            {"ts": _iso(4), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "dual_model_position"},
        ])
        since, exact = panelmod._incomplete_since("XUSDT")
        assert since is not None and since.isoformat() == oldest
        assert exact

    # ── Task 155: seri tail penceresini aşarsa süre kısalmasın ─────

    def test_incomplete_since_long_streak_lower_bound(self, panelmod):
        # Seri başlangıcı 16KB tail penceresinin DIŞINDA kalır:
        # pencere içindeki en eski INCOMPLETE ts'i döner ama
        # exact=False (alt-sınır) — süre yanlış "kesin" gösterilmez.
        recs = [{"ts": _iso(200 - i * 0.01), "symbol": "XUSDT",
                 "reason": "INCOMPLETE_POSITION_DATA",
                 "detail": "x" * 80,
                 "source": ("legacy_state_position" if i % 2 == 0
                            else "dual_model_position")}
                for i in range(600)]  # >> 16KB ve >200 kayıt
        self._write_audit(panelmod, recs)
        since, exact = panelmod._incomplete_since("XUSDT")
        assert since is not None
        assert not exact  # dürüst alt-sınır etiketi
        # Dönen ts pencere içindeki en eski kayıttır — en yeni
        # kayıttan kesinlikle daha eskidir (sayaç sıfırlanmadı).
        newest = panelmod._parse_ts(recs[-1]["ts"])
        assert since < newest

    def test_incomplete_since_exact_when_streak_breaks_in_window(
            self, panelmod):
        # Dosya pencereden büyük ama seri pencere İÇİNDE kırılıyor:
        # başlangıç kesin bilinir → exact=True.
        old = [{"ts": _iso(300), "symbol": "PADUSDT",
                "reason": "STALE_POSITION", "detail": "y" * 100}
               for _ in range(300)]  # dolgu: dosyayı 16KB üstüne it
        start = _iso(6)
        recs = old + [
            {"ts": _iso(7), "symbol": "XUSDT",
             "reason": "ORPHAN_POSITION", "detail": "d"},
            {"ts": start, "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"},
        ]
        self._write_audit(panelmod, recs)
        since, exact = panelmod._incomplete_since("XUSDT")
        assert since is not None and since.isoformat() == start
        assert exact

    def test_long_streak_critical_uses_en_az_label(self, panelmod):
        # Uçtan uca: pencereyi aşan uzun seride KRİTİK uyarı
        # "en az X saattir" alt-sınır etiketiyle görünür.
        self._incomplete_state(panelmod)
        recs = [{"ts": _iso(50 - i * 0.01), "symbol": "XUSDT",
                 "reason": "INCOMPLETE_POSITION_DATA",
                 "detail": "x" * 80,
                 "source": ("legacy_state_position" if i % 2 == 0
                            else "dual_model_position")}
                for i in range(600)]
        self._write_audit(panelmod, recs)
        w = panelmod._position_integrity_panel()["warnings"][0]
        assert w.startswith("KRİTİK:")
        assert "en az" in w and "saattir çözümsüz" in w

    def test_exact_streak_has_no_en_az_label(self, panelmod):
        # Küçük dosyada başlangıç kesin — "en az" etiketi YOK.
        self._incomplete_state(panelmod)
        self._write_audit(panelmod, [{
            "ts": _iso(6), "symbol": "XUSDT",
            "reason": "INCOMPLETE_POSITION_DATA", "detail": "d"}])
        w = panelmod._position_integrity_panel()["warnings"][0]
        assert w.startswith("KRİTİK:")
        assert "en az" not in w

    def test_alternating_sources_escalate_critical(self, panelmod):
        # Uçtan uca: dönüşümlü yazımlara rağmen KRİTİK eşiği ilk
        # tespit anına göre tetiklenir (Task 154 kabul kriteri).
        self._incomplete_state(panelmod)
        self._write_audit(panelmod, [
            {"ts": _iso(6), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "legacy_state_position"},
            {"ts": _iso(0.5), "symbol": "XUSDT",
             "reason": "INCOMPLETE_POSITION_DATA", "detail": "d",
             "source": "dual_model_position"},
        ])
        w = panelmod._position_integrity_panel()["warnings"][0]
        assert w.startswith("KRİTİK:")

    def test_healthy_position_no_warning(self, panelmod):
        self._write_state(panelmod, {
            "position": {"symbol": "ONDOUSDT", "entry": 0.95,
                         "quantity": 10, "opened_at": _iso(0.5)},
            "trades": [],
        })
        assert panelmod._position_integrity_panel()["warnings"] == []

    def test_recent_audit_tail_limit(self, panelmod):
        with panelmod.POSITION_AUDIT_PATH.open("w",
                                               encoding="utf-8") as fh:
            for i in range(50):
                fh.write(json.dumps({
                    "ts": _iso(1), "symbol": f"S{i}USDT",
                    "reason": "STALE_POSITION", "detail": "x"}) + "\n")
        recs = panelmod._recent_position_audit()
        assert len(recs) == panelmod.POSITION_AUDIT_RECENT_LIMIT
        assert recs[0]["symbol"] == "S49USDT"  # en yeni önce

    def test_status_endpoint_exposes_fields(self):
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        assert 'data["position_integrity_warnings"]' in src
        assert 'data["position_integrity_audit"]' in src

    def test_js_appends_integrity_warnings_to_banner(self):
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "position_integrity_warnings" in js
        idx = js.index("position_integrity_warnings")
        assert "showWarnings(warns)" in js[idx:idx + 400]


# ── Task 149: operatör onayı (görüldü / manuel kapatıldı) ──────────

class TestOperatorAck:
    @pytest.fixture()
    def ackmod(self, tmp_path, monkeypatch, appmod):
        monkeypatch.setattr(appmod, "POSITION_ACK_PATH",
                            tmp_path / "acks.json")
        monkeypatch.setattr(appmod, "STATE_PATH",
                            tmp_path / "state.json")
        monkeypatch.setattr(appmod, "load_config",
                            lambda: ({}, None))
        return appmod

    def _write_state(self, appmod, state: dict):
        appmod.STATE_PATH.write_text(
            json.dumps(state), encoding="utf-8")

    INCOMPLETE = {"symbol": "ONDOUSDT", "side": "LONG",
                  "opened_at": "2026-07-29T10:00:00+00:00"}

    def test_ack_writes_audit_and_store(self, ackmod):
        ackmod._record_position_ack(dict(self.INCOMPLETE), "op1")
        rec = json.loads(ackmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()[-1])
        assert rec["reason"] == "operator_ack"
        assert rec["symbol"] == "ONDOUSDT"
        assert "op1" in rec["detail"]
        assert ackmod._position_ack_active(dict(self.INCOMPLETE))

    def test_ack_suppresses_panel_warning(self, ackmod):
        self._write_state(ackmod, {"position": dict(self.INCOMPLETE),
                                   "trades": []})
        assert ackmod._position_integrity_panel()["warnings"]
        ackmod._record_position_ack(dict(self.INCOMPLETE), "op1")
        panel = ackmod._position_integrity_panel()
        assert panel["warnings"] == []
        # Audit geçmişinde görünür kalır.
        assert any(r["reason"] == "operator_ack"
                   for r in panel["recent_audit"])

    def test_ack_scoped_to_record_new_position_reappears(self,
                                                         ackmod):
        ackmod._record_position_ack(dict(self.INCOMPLETE), "op1")
        newer = dict(self.INCOMPLETE,
                     opened_at="2026-07-30T09:00:00+00:00")
        assert not ackmod._position_ack_active(newer)

    def test_endpoint_acks_incomplete_position(self, ackmod):
        self._write_state(ackmod, {"position": dict(self.INCOMPLETE),
                                   "trades": []})
        ackmod.app.config["TESTING"] = True
        ackmod.app.config["WTF_CSRF_ENABLED"] = False
        with ackmod.app.test_client() as c:
            r = c.post("/api/positions/integrity/ack",
                       json={"symbol": "ONDOUSDT"})
        assert r.status_code == 200 and r.get_json()["ok"]
        assert ackmod._position_ack_active(dict(self.INCOMPLETE))

    def test_endpoint_rejects_healthy_position(self, ackmod):
        self._write_state(ackmod, {
            "position": {"symbol": "ONDOUSDT", "entry": 0.95,
                         "quantity": 10, "opened_at": _iso(0.5)},
            "trades": []})
        ackmod.app.config["TESTING"] = True
        ackmod.app.config["WTF_CSRF_ENABLED"] = False
        with ackmod.app.test_client() as c:
            r = c.post("/api/positions/integrity/ack",
                       json={"symbol": "ONDOUSDT"})
        assert r.status_code == 409
        assert r.get_json()["message"] == "NOT_INCOMPLETE"

    def test_endpoint_rejects_unknown_symbol(self, ackmod):
        self._write_state(ackmod, {"position": None, "trades": []})
        ackmod.app.config["TESTING"] = True
        ackmod.app.config["WTF_CSRF_ENABLED"] = False
        with ackmod.app.test_client() as c:
            r = c.post("/api/positions/integrity/ack",
                       json={"symbol": "XUSDT"})
        assert r.status_code == 404

    def test_endpoint_requires_symbol(self, ackmod):
        ackmod.app.config["TESTING"] = True
        ackmod.app.config["WTF_CSRF_ENABLED"] = False
        with ackmod.app.test_client() as c:
            r = c.post("/api/positions/integrity/ack", json={})
        assert r.status_code == 400

    def test_operation_raw_excludes_acked_incomplete(self):
        # _operation_raw acked INCOMPLETE kaydı aktif listeye almaz
        # (kaynak kodu sözleşmesi — davranış birim testleri yukarıda).
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        assert "_position_ack_active(_pos)" in src

    def test_js_has_ack_button_and_endpoint(self):
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        assert "Onayla / Manuel Kapat" in js
        assert "/api/positions/integrity/ack" in js
        assert "data-ack-symbol" in js


# ── Dual-model pozisyon durumu + exit korumaları ───────────────────

@pytest.fixture()
def dmiso(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "RUNTIME_PATH",
                        tmp_path / "dual_model_runtime.json")
    return tmp_path


def _rt(positions: dict) -> dict:
    return {"positions": positions, "trades": [], "core_list": [],
            "opportunity_list": []}


def _dm_pos(**over) -> dict:
    base = {"symbol": "ONDOUSDT", "model": dm.MODEL_CORE,
            "side": "LONG", "entry": 0.95, "quantity": 100.0,
            "notional_usdt": 95.0, "opened_at": _iso(0.2),
            "opened_ts": 0.0, "peak": 0.95, "tp": 1.0, "sl": 0.9,
            "trailing_pct": 0.5, "max_hold_minutes": 60,
            "confidence": 70, "config_version": "BASE"}
    base.update(over)
    return base


class TestDualPositionStatus:
    def test_price_refresh_failed_status(self, dmiso, monkeypatch):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {})
        snap = dm.snapshot(with_prices=True)
        p = snap["positions"][0]
        assert p["position_status"] == "PRICE_REFRESH_FAILED"
        assert p["current_price"] is None
        assert p["unrealized_net_pnl"] is None  # uydurma PnL yok
        assert snap["live_orders"] == "DISABLED"

    def test_incomplete_status_when_fields_missing(self, dmiso,
                                                   monkeypatch):
        bad = _dm_pos(); del bad["quantity"]
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": bad})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {"ONDOUSDT": 0.97})
        snap = dm.snapshot(with_prices=True)
        p = snap["positions"][0]
        assert p["position_status"] == "INCOMPLETE_POSITION_DATA"
        assert p["unrealized_net_pnl"] is None

    def test_active_when_healthy(self, dmiso, monkeypatch):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {"ONDOUSDT": 0.97})
        p = dm.snapshot(with_prices=True)["positions"][0]
        assert p["position_status"] == "ACTIVE"
        assert p["unrealized_net_pnl"] is not None


class TestExitGuards:
    def test_monitor_skips_incomplete_and_flags(self, dmiso):
        bad = _dm_pos(quantity=0)  # geçersiz miktar
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": bad})))
        closed = dm.monitor_positions(
            lambda s: 10.0, dm.get_config())  # fiyat TP üstünde bile
        assert closed == []  # eksik veriyle exit YOK
        rt = json.loads((dmiso / "dual_model_runtime.json")
                        .read_text())
        assert "ONDOUSDT" in rt["positions"]  # pozisyona dokunulmadı
        assert "pozisyon verisi eksik" in (rt.get("last_error") or "")

    def test_monitor_skips_when_price_missing(self, dmiso):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        assert dm.monitor_positions(
            lambda s: None, dm.get_config()) == []

    def test_manual_close_rejects_incomplete(self, dmiso):
        bad = _dm_pos(); bad["entry"] = None
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": bad})))
        ok, msg = dm.manual_close("ONDOUSDT", price=0.97)
        assert not ok and msg == "INCOMPLETE_POSITION_DATA"
        rt = json.loads((dmiso / "dual_model_runtime.json")
                        .read_text())
        assert "ONDOUSDT" in rt["positions"]

    def test_manual_close_rejects_without_fresh_price(self, dmiso,
                                                      monkeypatch):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt({"ONDOUSDT": _dm_pos()})))
        monkeypatch.setattr(dm, "fetch_spot_prices",
                            lambda syms: {})
        ok, msg = dm.manual_close("ONDOUSDT")
        assert not ok and msg == "PRICE_UNAVAILABLE"


# ── Task 152: dual-model INCOMPLETE kaydının operatör onayı ────────

class TestDualOperatorAck:
    def _write_rt(self, dmiso, positions):
        (dmiso / "dual_model_runtime.json").write_text(
            json.dumps(_rt(positions)))

    def test_ack_removes_incomplete_from_runtime(self, dmiso):
        bad = _dm_pos(); del bad["quantity"]
        self._write_rt(dmiso, {"ONDOUSDT": bad})
        ok, msg, removed = dm.acknowledge_incomplete("ondousdt")
        assert ok and msg == "ACKED"
        assert removed["symbol"] == "ONDOUSDT"
        rt = json.loads(
            (dmiso / "dual_model_runtime.json").read_text())
        assert rt["positions"] == {}

    def test_ack_rejects_healthy_position(self, dmiso):
        self._write_rt(dmiso, {"ONDOUSDT": _dm_pos()})
        ok, msg, removed = dm.acknowledge_incomplete("ONDOUSDT")
        assert not ok and msg == "NOT_INCOMPLETE" and removed is None
        rt = json.loads(
            (dmiso / "dual_model_runtime.json").read_text())
        assert "ONDOUSDT" in rt["positions"]  # kayda dokunulmadı

    def test_ack_unknown_symbol(self, dmiso):
        self._write_rt(dmiso, {})
        ok, msg, _ = dm.acknowledge_incomplete("NOPEUSDT")
        assert not ok and msg == "POSITION_NOT_FOUND"

    @pytest.fixture()
    def client(self, appmod, dmiso):
        appmod.app.config["TESTING"] = True
        appmod.app.config["WTF_CSRF_ENABLED"] = False
        with appmod.app.test_client() as c:
            yield c

    def test_endpoint_acks_dual_incomplete(self, client, appmod,
                                           dmiso):
        bad = _dm_pos(); del bad["quantity"]
        self._write_rt(dmiso, {"ONDOUSDT": bad})
        r = client.post("/api/positions/integrity/ack",
                        json={"symbol": "ONDOUSDT",
                              "source": "dual"})
        assert r.status_code == 200 and r.get_json()["ok"]
        rt = json.loads(
            (dmiso / "dual_model_runtime.json").read_text())
        assert rt["positions"] == {}
        rec = json.loads(appmod.POSITION_AUDIT_PATH.read_text(
            encoding="utf-8").strip().splitlines()[-1])
        assert rec["reason"] == "operator_ack"
        assert rec["symbol"] == "ONDOUSDT"
        assert rec["source"] == "dual_model_position"

    def test_endpoint_rejects_healthy_dual(self, client, dmiso):
        self._write_rt(dmiso, {"ONDOUSDT": _dm_pos()})
        r = client.post("/api/positions/integrity/ack",
                        json={"symbol": "ONDOUSDT",
                              "source": "dual"})
        assert r.status_code == 409
        assert r.get_json()["message"] == "NOT_INCOMPLETE"
        rt = json.loads(
            (dmiso / "dual_model_runtime.json").read_text())
        assert "ONDOUSDT" in rt["positions"]

    def test_endpoint_dual_unknown_symbol(self, client, dmiso):
        self._write_rt(dmiso, {})
        r = client.post("/api/positions/integrity/ack",
                        json={"symbol": "NOPEUSDT",
                              "source": "dual"})
        assert r.status_code == 404
        assert r.get_json()["message"] == "POSITION_NOT_FOUND"


# ── UI sözleşmesi ──────────────────────────────────────────────────

class TestUiContract:
    def test_status_codes_rendered_honestly(self):
        js = (ROOT / "static/js/trading_home.js").read_text(
            encoding="utf-8")
        for code in ("PRICE_REFRESH_FAILED", "RECONCILIATION_REQUIRED",
                     "ORPHAN_POSITION", "INCOMPLETE_POSITION_DATA",
                     "STALE_POSITION"):
            assert code in js, code
        assert ("Çıkış değerlendirmesi durduruldu — "
                "pozisyon verisi eksik") in js
        # Varsayılan artık körlemesine "Yönetiliyor" değil.
        assert 'STATUS_TR[p.position_status] || "Yönetiliyor"' not in js
        # Task 152: eksik veride Kapat yerine onay butonu render
        # edilir (dual kaynağı işaretli).
        assert 'data-ack-source=\\"dual\\"' in js

    def test_gitignore_covers_audit(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "alpha20_v1/position_integrity_audit.jsonl" in gi
