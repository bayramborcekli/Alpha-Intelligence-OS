"""Mission 2100 — Agent 10: Yayın doğrulayıcı (v1.1.0).

Bu modül YENİ işlev, mimari veya kamu API değişikliği içermez.
Resmî yayının bildirimsel sözleşmesini taşır ve saf doğrulama
fonksiyonları sunar: sürüm kimliği, agent zinciri PASS durumu,
sertifika çapraz referansları ve dondurulmuş modül bütünlüğü
(version_manifest.json içindeki SHA-256 imzaları test paketi
tarafından canlı kaynağa uygulanır).

Dosya G/Ç YOKTUR: manifest içeriğini test katmanı okur ve
buradaki saf fonksiyonlara verir.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Tuple

from regression_runner import (AGENT_CHAIN, BASELINE_COMMIT,
                               MISSION_2000_BASELINE)
from security_validation import MISSION_2100_MODULES
from system_certification import (ARCHITECTURE_CERTIFICATE,
                                  MISSION_2100_READINESS,
                                  REGRESSION_CERTIFICATE,
                                  SECURITY_CERTIFICATE,
                                  SOAK_CERTIFICATE)

__all__ = ["RELEASE_VERSION", "RELEASE_NAME", "MISSION",
           "MISSION_STATUS", "AGENT_RESULTS",
           "RELEASE_CERTIFICATES", "COMPATIBILITY_REPORT",
           "RELEASE_MANIFEST_REQUIRED_KEYS",
           "verify_manifest_identity",
           "verify_agent_results", "verify_module_coverage"]

# ── Resmî yayın kimliği ────────────────────────────────────────
RELEASE_VERSION = "1.1.0"
RELEASE_NAME = "Controlled Execution"
MISSION = "2100"
MISSION_STATUS = "COMPLETE"

# ── Agent zinciri sonuçları — tamamı ZORUNLU PASS ─────────────
AGENT_RESULTS = MappingProxyType({
    "MISSION_2000": "FROZEN",
    "01": "PASS", "02": "PASS", "03": "PASS", "04": "PASS",
    "05": "PASS", "06": "PASS", "07": "PASS", "08": "PASS",
    "09": "PASS",
})

# ── Yayın sertifikaları (kanıt: Agent 09 + yayın test paketi) ──
RELEASE_CERTIFICATES = MappingProxyType({
    "architecture": ARCHITECTURE_CERTIFICATE,
    "security": SECURITY_CERTIFICATE,
    "regression": REGRESSION_CERTIFICATE,
    "soak": SOAK_CERTIFICATE,
    "readiness": MISSION_2100_READINESS,
})

# ── Uyumluluk raporu ──────────────────────────────────────────
COMPATIBILITY_REPORT = MappingProxyType({
    "baseline_release": "1.0.0",
    "baseline_name": "Execution Core",
    "public_api_changes": 0,
    "breaking_changes": 0,
    "mission_2000_modified": False,
    "backward_compatible": True,
    "mission_2000_baseline": MISSION_2000_BASELINE,
    "agent_09_baseline_commit": BASELINE_COMMIT,
})

# version_manifest.json zorunlu anahtarları
RELEASE_MANIFEST_REQUIRED_KEYS = (
    "product", "version", "release_name", "mission",
    "mission_status", "agents", "certified_modules",
    "module_sha256", "total_regression_at_freeze",
    "known_skip_count")


def verify_manifest_identity(
        manifest: Mapping[str, object]) -> Tuple[str, ...]:
    """Manifest kimlik alanlarını sözleşmeyle karşılaştırır;
    sapmaların steril listesini döndürür (boş = temiz)."""
    findings = []
    for key in RELEASE_MANIFEST_REQUIRED_KEYS:
        if key not in manifest:
            findings.append(f"MISSING_KEY:{key}")
    if manifest.get("version") != RELEASE_VERSION:
        findings.append("VERSION_MISMATCH")
    if manifest.get("release_name") != RELEASE_NAME:
        findings.append("RELEASE_NAME_MISMATCH")
    if manifest.get("mission") != MISSION:
        findings.append("MISSION_MISMATCH")
    if manifest.get("mission_status") != MISSION_STATUS:
        findings.append("MISSION_STATUS_MISMATCH")
    return tuple(findings)


def verify_agent_results(
        manifest: Mapping[str, object]) -> Tuple[str, ...]:
    """Manifest'teki agent sonuçları sözleşmeyle birebir aynı
    olmalıdır; tüm agentlar PASS şarttır."""
    findings = []
    agents = manifest.get("agents")
    if not isinstance(agents, Mapping):
        return ("AGENTS_NOT_MAPPING",)
    if dict(agents) != dict(AGENT_RESULTS):
        findings.append("AGENT_RESULTS_MISMATCH")
    for agent, result in AGENT_RESULTS.items():
        if agent != "MISSION_2000" and result != "PASS":
            findings.append(f"AGENT_NOT_PASS:{agent}")
    return tuple(findings)


def verify_module_coverage(
        manifest: Mapping[str, object]) -> Tuple[str, ...]:
    """module_sha256 anahtar kümesi sertifikalı modül kümesiyle
    birebir aynı olmalıdır (eksik/fazla modül = ihlal)."""
    hashes = manifest.get("module_sha256")
    if not isinstance(hashes, Mapping):
        return ("HASHES_NOT_MAPPING",)
    expected = set(MISSION_2100_MODULES)
    actual = set(hashes)
    findings = []
    for missing in sorted(expected - actual):
        findings.append(f"MODULE_NOT_PINNED:{missing}")
    for extra in sorted(actual - expected):
        findings.append(f"UNEXPECTED_MODULE:{extra}")
    return tuple(findings)
