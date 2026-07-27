"""Mission 2100 — Agent 09: Regresyon manifestosu testleri.

Manifesto bütünlüğü, teslim zincirinin monoton artışı, her
agent'ın test modüllerinin CANLI test ağacında varlığı ve bilinen
atlama kümesi dışında atlama olmaması doğrulanır.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import regression_runner as rr  # noqa: E402

AGENT_MODULE_CASES = [
    (agent, module)
    for agent, modules in rr.AGENT_TEST_MODULES.items()
    for module in modules]


class TestManifestIntegrity:
    def test_mission_identity(self):
        assert rr.MISSION == "2100"
        assert rr.AGENT == "09"

    def test_baseline_matches_chain(self):
        commit, count = rr.AGENT_CHAIN["08"]
        assert commit == rr.BASELINE_COMMIT == "30eee0b"
        assert count == rr.BASELINE_REGRESSION == 8137

    def test_mission_2000_baseline(self):
        assert rr.MISSION_2000_BASELINE == ("01aa429", 3704)
        assert rr.MISSION_2000_FULL_PACKAGE == \
            ("a45dde3", 4375)

    def test_chain_monotonic(self):
        counts = [count for _, count in
                  rr.AGENT_CHAIN.values()
                  if count is not None]
        assert counts == sorted(counts)
        assert len(counts) == len(set(counts))

    @pytest.mark.parametrize("agent", ["01", "02", "03", "04",
                                       "05", "06", "07", "08",
                                       "HF-001",
                                       "CORE_FREEZE"])
    def test_chain_complete(self, agent):
        assert agent in rr.AGENT_CHAIN

    def test_chain_immutable(self):
        with pytest.raises(TypeError):
            rr.AGENT_CHAIN["08"] = ("x", 0)

    def test_manifest_immutable(self):
        with pytest.raises(TypeError):
            rr.REGRESSION_MANIFEST["mission"] = "0"

    def test_test_modules_immutable(self):
        with pytest.raises(TypeError):
            rr.AGENT_TEST_MODULES["01"] = ()

    def test_manifest_consistent(self):
        manifest = rr.REGRESSION_MANIFEST
        assert manifest["baseline_commit"] == \
            rr.BASELINE_COMMIT
        assert manifest["baseline_regression"] == \
            rr.BASELINE_REGRESSION
        assert manifest["known_skip_count"] == \
            rr.KNOWN_SKIP_COUNT

    def test_commit_hashes_sterile(self):
        for commit, _ in rr.AGENT_CHAIN.values():
            assert len(commit) == 7
            assert commit == commit.lower()


class TestLiveTestTree:
    @pytest.mark.parametrize("agent,module",
                             AGENT_MODULE_CASES)
    def test_agent_test_module_exists(self, agent, module):
        path = ROOT / "tests" / f"{module}.py"
        assert path.is_file(), f"Agent {agent}: {module}"

    @pytest.mark.parametrize("agent,module",
                             AGENT_MODULE_CASES)
    def test_agent_test_module_nonempty(self, agent, module):
        source = (ROOT / "tests" / f"{module}.py").read_text(
            encoding="utf-8")
        assert "def test" in source

    def test_mission_2000_regression_tests_present(self):
        assert (ROOT / "tests" /
                "test_execution_regression_manifest.py"
                ).is_file()


class TestSkipDiscipline:
    def test_known_skip_count(self):
        assert rr.KNOWN_SKIP_COUNT == 1
        assert len(rr.KNOWN_SKIPS) == 1

    @staticmethod
    def _skip_nodes(source):
        """AST tabanlı atlama tespiti: pytest.skip çağrıları ve
        mark.skip / mark.skipif dekoratörleri dahil."""
        import ast
        found = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Attribute) and \
                    node.attr in ("skip", "skipif"):
                found.append(node.attr)
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Name) and \
                    node.func.id == "skip":
                found.append("skip")
        return found

    def test_no_skip_markers_in_mission_2100_tests(self):
        """Mission 2100 agent test paketlerinde skip OLAMAZ —
        bilinen tek atlama Mission 2000 güvenlik paketindedir
        (KNOWN_SKIPS)."""
        for _, module in AGENT_MODULE_CASES:
            path = ROOT / "tests" / f"{module}.py"
            if f"tests/{module}.py" in rr.KNOWN_SKIPS:
                continue
            source = path.read_text(encoding="utf-8")
            assert self._skip_nodes(source) == [], module

    def test_coverage_map_spans_full_chain(self):
        """Her AGENT_CHAIN girdisi AGENT_TEST_MODULES'ta
        açıkça temsil edilmelidir (boş demet bilinçli)."""
        chain_keys = set(rr.AGENT_CHAIN)
        map_keys = set(rr.AGENT_TEST_MODULES)
        assert chain_keys <= map_keys, \
            chain_keys - map_keys

    def test_only_hotfix_may_be_empty(self):
        empty = [agent for agent, modules in
                 rr.AGENT_TEST_MODULES.items()
                 if modules == ()]
        assert empty == ["HF-001"]

    def test_known_skip_is_not_mission_2100(self):
        for path in rr.KNOWN_SKIPS:
            assert "controlled_execution" not in path
            assert "paper" not in path or \
                "execution_security" in path
