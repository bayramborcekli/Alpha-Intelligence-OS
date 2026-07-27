"""Mission 2100 — Agent 09: Değişmez regresyon manifestosu.

Bu modül YENİ iş işlevi içermez. Mission 2100 teslim zincirinin
doğrulanmış son durumunu değişmez sabitler olarak kayda geçirir.
Değerler icat edilmemiştir: her agent'ın teslim commit hash'i ve
o teslimdeki tam regresyon sayısı birebir kullanılmıştır.

Test paketleri bu manifestoyu canlı test ağacına uygular: her
agent'ın test modülleri mevcut olmalı, zincir monoton artmalı ve
bilinen atlama (skip) kümesi dışında atlama OLMAMALIDIR.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["MISSION", "AGENT", "MISSION_2000_BASELINE",
           "MISSION_2000_FULL_PACKAGE", "AGENT_CHAIN",
           "BASELINE_COMMIT", "BASELINE_REGRESSION",
           "AGENT_TEST_MODULES", "KNOWN_SKIPS",
           "REGRESSION_MANIFEST"]

MISSION = "2100"
AGENT = "09"

# Mission 2000 tabanı (manifesto ile tam paket bilinçli ayrı)
MISSION_2000_BASELINE = ("01aa429", 3704)
MISSION_2000_FULL_PACKAGE = ("a45dde3", 4375)

# Mission 2100 teslim zinciri: agent → (commit, tam regresyon)
AGENT_CHAIN = MappingProxyType({
    "CORE_FREEZE": ("03e181d", None),
    "01": ("4304527", 4619),
    "02": ("69bd05c", 5215),
    "03": ("32f4a3a", 5585),
    "04": ("bf2a21d", 5994),
    "05": ("459ca5a", 6392),
    "06": ("ba896ca", 6895),
    "HF-001": ("ffdf3f9", 6927),
    "07": ("df0fb04", 7667),
    "08": ("30eee0b", 8137),
})

# Agent 08 PASS temel değerleri (Agent 09 tabanı)
BASELINE_COMMIT = "30eee0b"
BASELINE_REGRESSION = 8137

# Agent → test modülleri (tam kapsam kanıtı için varlık şartı).
# ZORUNLU: her AGENT_CHAIN anahtarı burada temsil edilmelidir;
# boş demet BİLİNÇLİ bir karardır ve gerekçesi yorumda durur.
AGENT_TEST_MODULES = MappingProxyType({
    # Execution Core dondurması — Mission 2000 manifestosu ile
    # kanıtlanır (Agent 09/Mission 2000 teslimi).
    "CORE_FREEZE": ("test_execution_regression_manifest",),
    "01": ("test_controlled_execution_foundation",),
    "02": ("test_runtime_models",
           "test_runtime_architecture"),
    "03": ("test_paper_ledger", "test_paper_broker",
           "test_paper_architecture"),
    "04": ("test_paper_execution_service",
           "test_paper_execution_mapper",
           "test_paper_execution_architecture"),
    # HF-001 dashboard Spot kart düzeltmesi: kalıcı kapsamı tam
    # regresyon paketi taşır (ayrı test modülü bilinçli yok).
    "HF-001": (),
    "05": ("test_shadow_mode", "test_shadow_comparator",
           "test_shadow_architecture"),
    "06": ("test_micro_live_authorization",
           "test_micro_live_policy",
           "test_micro_live_architecture"),
    "07": ("test_order_lifecycle", "test_reconciliation",
           "test_reconciliation_architecture"),
    "08": ("test_controlled_execution_api",
           "test_controlled_execution_router",
           "test_controlled_execution_architecture"),
    "09": ("test_security_validation", "test_soak",
           "test_regression", "test_certification"),
})

# Bilinen ve GEREKÇELİ atlamalar — kritik test DEĞİLDİR.
# (Mission 2000 güvenlik paketi: bildirimsel dispatch tablosu
# muafiyeti; kapsamı kendi mimari testleriyle kapatılmıştır.)
KNOWN_SKIPS = (
    "tests/test_execution_security.py",
)
KNOWN_SKIP_COUNT = 1

REGRESSION_MANIFEST = MappingProxyType({
    "mission": MISSION,
    "agent": AGENT,
    "baseline_commit": BASELINE_COMMIT,
    "baseline_regression": BASELINE_REGRESSION,
    "mission_2000_baseline": MISSION_2000_BASELINE,
    "known_skip_count": KNOWN_SKIP_COUNT,
})
