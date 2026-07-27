"""Mission 2100 — Agent 10: Yayın doğrulama testleri.

v1.1.0 "Controlled Execution" yayın sözleşmesi:
sürüm kimliği, VERSION dosyası, manifest bütünlüğü, agent
zinciri PASS durumu, sertifika çapraz referansları, uyumluluk
raporu ve yayın belgelerinin zorunlu bölümleri.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import release_validator as rv  # noqa: E402
import system_certification as sc  # noqa: E402
from regression_runner import AGENT_CHAIN  # noqa: E402

MANIFEST = json.loads(
    (ROOT / "version_manifest.json").read_text(
        encoding="utf-8"))

DOCS = {
    "release_notes": (ROOT / "release_notes.md").read_text(
        encoding="utf-8"),
    "completion": (ROOT / "mission_2100_completion.md")
    .read_text(encoding="utf-8"),
    "certificate": (ROOT / "mission_2100_certificate.md")
    .read_text(encoding="utf-8"),
}


class TestReleaseIdentity:
    def test_version(self):
        assert rv.RELEASE_VERSION == "1.1.0"

    def test_release_name(self):
        assert rv.RELEASE_NAME == "Controlled Execution"

    def test_mission_status(self):
        assert rv.MISSION == "2100"
        assert rv.MISSION_STATUS == "COMPLETE"

    def test_version_file_released(self):
        assert (ROOT / "VERSION").read_text(
            encoding="utf-8").strip() == "1.1.0"

    def test_version_module_agrees(self):
        from version import get_version
        assert get_version() == "1.1.0"

    def test_no_prerelease_suffix(self):
        assert "-" not in rv.RELEASE_VERSION
        assert "alpha" not in rv.RELEASE_VERSION


class TestAgentResults:
    @pytest.mark.parametrize("agent", ["01", "02", "03", "04",
                                       "05", "06", "07", "08",
                                       "09"])
    def test_agent_pass(self, agent):
        assert rv.AGENT_RESULTS[agent] == "PASS"

    def test_mission_2000_frozen(self):
        assert rv.AGENT_RESULTS["MISSION_2000"] == "FROZEN"

    def test_results_immutable(self):
        with pytest.raises(TypeError):
            rv.AGENT_RESULTS["01"] = "FAIL"

    @pytest.mark.parametrize("agent", ["01", "02", "03", "04",
                                       "05", "06", "07", "08"])
    def test_agent_in_delivery_chain(self, agent):
        assert agent in AGENT_CHAIN


class TestManifest:
    @pytest.mark.parametrize(
        "key", rv.RELEASE_MANIFEST_REQUIRED_KEYS)
    def test_required_key_present(self, key):
        assert key in MANIFEST

    def test_identity_clean(self):
        assert rv.verify_manifest_identity(MANIFEST) == ()

    def test_agents_clean(self):
        assert rv.verify_agent_results(MANIFEST) == ()

    def test_module_coverage_clean(self):
        assert rv.verify_module_coverage(MANIFEST) == ()

    def test_product_name(self):
        assert MANIFEST["product"] == "Alpha Intelligence OS"

    def test_known_skip_count(self):
        assert MANIFEST["known_skip_count"] == 1

    def test_total_regression_recorded(self):
        total = MANIFEST["total_regression_at_freeze"]
        assert isinstance(total, int) and total >= 11000

    @pytest.mark.parametrize("agent,expected",
                             list(AGENT_CHAIN.items()),
                             ids=list(AGENT_CHAIN))
    def test_chain_snapshot_matches(self, agent, expected):
        entry = MANIFEST["agent_chain"][agent]
        assert entry["commit"] == expected[0]
        assert entry["regression"] == expected[1]

    @pytest.mark.parametrize("agent,result", [
        ("MISSION_2000", "FROZEN"), ("01", "PASS"),
        ("02", "PASS"), ("03", "PASS"), ("04", "PASS"),
        ("05", "PASS"), ("06", "PASS"), ("07", "PASS"),
        ("08", "PASS"), ("09", "PASS")])
    def test_manifest_agent_result(self, agent, result):
        assert MANIFEST["agents"][agent] == result

    def test_mission_2000_baseline_snapshot(self):
        entry = MANIFEST["mission_2000_baseline"]
        assert (entry["commit"], entry["regression"]) == \
            ("01aa429", 3704)


class TestValidatorCorrectness:
    """Doğrulayıcı sahte-temiz OLMAMALI."""

    def test_detects_missing_key(self):
        bad = {k: v for k, v in MANIFEST.items()
               if k != "version"}
        findings = rv.verify_manifest_identity(bad)
        assert "MISSING_KEY:version" in findings
        assert "VERSION_MISMATCH" in findings

    def test_detects_wrong_version(self):
        bad = dict(MANIFEST)
        bad["version"] = "9.9.9"
        assert "VERSION_MISMATCH" in \
            rv.verify_manifest_identity(bad)

    def test_detects_wrong_status(self):
        bad = dict(MANIFEST)
        bad["mission_status"] = "OPEN"
        assert "MISSION_STATUS_MISMATCH" in \
            rv.verify_manifest_identity(bad)

    def test_detects_agent_tamper(self):
        bad = dict(MANIFEST)
        bad["agents"] = dict(MANIFEST["agents"],
                             **{"09": "FAIL"})
        assert "AGENT_RESULTS_MISMATCH" in \
            rv.verify_agent_results(bad)

    def test_detects_missing_module(self):
        bad = dict(MANIFEST)
        hashes = dict(MANIFEST["module_sha256"])
        hashes.pop("controlled_execution_api")
        bad["module_sha256"] = hashes
        assert ("MODULE_NOT_PINNED:controlled_execution_api"
                in rv.verify_module_coverage(bad))

    def test_detects_extra_module(self):
        bad = dict(MANIFEST)
        bad["module_sha256"] = dict(
            MANIFEST["module_sha256"], rogue_module="00")
        assert "UNEXPECTED_MODULE:rogue_module" in \
            rv.verify_module_coverage(bad)

    def test_non_mapping_rejected(self):
        assert rv.verify_agent_results(
            {"agents": "PASS"}) == ("AGENTS_NOT_MAPPING",)
        assert rv.verify_module_coverage(
            {"module_sha256": []}) == ("HASHES_NOT_MAPPING",)


class TestCertificates:
    @pytest.mark.parametrize("kind", ["architecture",
                                      "security",
                                      "regression", "soak",
                                      "readiness"])
    def test_certificate_wired(self, kind):
        assert kind in rv.RELEASE_CERTIFICATES

    def test_certificates_are_agent09_certificates(self):
        assert rv.RELEASE_CERTIFICATES["security"] is \
            sc.SECURITY_CERTIFICATE
        assert rv.RELEASE_CERTIFICATES["readiness"] is \
            sc.MISSION_2100_READINESS

    @pytest.mark.parametrize("counter", [
        "exchange_write", "secret_exposure",
        "credential_logging"])
    def test_security_counters_zero(self, counter):
        assert rv.RELEASE_CERTIFICATES[
            "security"][counter] == 0

    def test_readiness_ready(self):
        assert rv.RELEASE_CERTIFICATES[
            "readiness"]["status"] == "READY"


class TestCompatibility:
    def test_baseline_release(self):
        assert rv.COMPATIBILITY_REPORT[
            "baseline_release"] == "1.0.0"
        assert rv.COMPATIBILITY_REPORT[
            "baseline_name"] == "Execution Core"

    @pytest.mark.parametrize("counter", [
        "public_api_changes", "breaking_changes"])
    def test_zero_breaking(self, counter):
        assert rv.COMPATIBILITY_REPORT[counter] == 0

    def test_backward_compatible(self):
        assert rv.COMPATIBILITY_REPORT[
            "backward_compatible"] is True
        assert rv.COMPATIBILITY_REPORT[
            "mission_2000_modified"] is False

    def test_report_immutable(self):
        with pytest.raises(TypeError):
            rv.COMPATIBILITY_REPORT["breaking_changes"] = 1


class TestDocumentation:
    @pytest.mark.parametrize("doc", sorted(DOCS))
    def test_doc_exists_nonempty(self, doc):
        assert len(DOCS[doc]) > 500

    @pytest.mark.parametrize("doc", sorted(DOCS))
    @pytest.mark.parametrize("token", ["1.1.0",
                                       "Controlled Execution",
                                       "Mission 2100",
                                       "COMPLETE"])
    def test_doc_identity_tokens(self, doc, token):
        assert token in DOCS[doc] or token.upper() in \
            DOCS[doc].upper()

    @pytest.mark.parametrize("section", [
        "Mission Summary", "Architecture Summary",
        "Release Notes", "Known Limitations",
        "Future Mission Entry Point"])
    def test_release_notes_sections(self, section):
        assert section in DOCS["release_notes"]

    @pytest.mark.parametrize("token", [
        "Exchange Write = 0", "Secret Exposure = 0",
        "Production Network Write = 0", "Credential Leak = 0",
        "API Exposure = 0"])
    def test_certificate_security_counters(self, token):
        assert token in DOCS["certificate"]

    @pytest.mark.parametrize("commit", [
        commit for commit, _ in AGENT_CHAIN.values()])
    def test_completion_lists_chain_commits(self, commit):
        assert commit in DOCS["completion"]

    def test_known_limitation_live_locked(self):
        assert "LIVE modu yoktur" in DOCS["release_notes"]
