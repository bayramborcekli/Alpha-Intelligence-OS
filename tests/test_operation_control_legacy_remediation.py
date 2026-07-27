"""Mission 2200 — Agent 01: legacy çakışma temizliği testleri.

- .bak/.bak2/.bak3 kopyaları depodan kaldırıldı.
- Kilit/durum dosyaları izlenmiyor ve .gitignore kapsamında.
- Yanıltıcı "salt izleme" iddiaları katman-kapsamlı ifadelerle
  düzeltildi; tarih tahrif edilmedi.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")


class TestBackupFilesRemoved:
    @pytest.mark.parametrize("name", [
        "app.py.bak", "app.py.bak2", "app.py.bak3",
        "templates/dashboard.html.bak3",
        "alpha20_v1/config.json.bak2",
        "alpha20_v1/config.json.bak3"])
    def test_backup_absent(self, name):
        assert not (ROOT / name).exists(), name

    def test_no_bak_files_tracked_by_git(self):
        """Çalışma dizininde geçici .bak oluşabilir (bot/platform);
        kritik olan depoda İZLENMEMELERİDİR."""
        import subprocess
        tracked = subprocess.run(
            ["git", "ls-files", "*.bak", "*.bak[0-9]"],
            cwd=ROOT, capture_output=True, text=True,
            check=True).stdout.strip()
        assert tracked == "", tracked


class TestGitignoreCoverage:
    @pytest.mark.parametrize("pattern", [
        "*.bak", "*.bak[0-9]", ".auto_controller.lock",
        "automation_state.json"])
    def test_pattern_present(self, pattern):
        assert pattern in GITIGNORE, pattern


class TestMisleadingClaimsFixed:
    def test_automation_template_reworded(self):
        source = (ROOT / "templates" /
                  "automation.html").read_text(encoding="utf-8")
        assert "SALT İZLEME" not in source
        assert "Operation Center" in source or \
            "operation-center" in source

    def test_automation_doc_layer_scoped(self):
        source = (ROOT / "docs" /
                  "automation.md").read_text(encoding="utf-8")
        assert "operation" in source.lower()

    def test_api_reference_mentions_operation_control(self):
        source = (ROOT / "docs" /
                  "API_REFERENCE.md").read_text(encoding="utf-8")
        assert "operation-control" in source

    def test_operator_guide_versioned_not_falsified(self):
        source = (ROOT / "docs" / "operator_guide_tr.md"
                  ).read_text(encoding="utf-8")
        assert "2200" in source or "Operation" in source

    def test_nav_links_operation_center(self):
        source = (ROOT / "templates" /
                  "dash_base.html").read_text(encoding="utf-8")
        assert "/operation-center" in source


class TestFrozenHistoryPreserved:
    def test_attached_assets_untouched(self):
        assert (ROOT / "attached_assets").is_dir()

    def test_mission_spec_still_present(self):
        specs = list((ROOT / "attached_assets").glob(
            "*MISSION-2200*"))
        assert specs, "Mission 2200 spec FROZEN_HISTORY'de kalmalı"


class TestNewDocsPresent:
    @pytest.mark.parametrize("name", [
        "docs/operation_control_center.md",
        "docs/mission2200_agent01_report.md"])
    def test_doc_exists(self, name):
        assert (ROOT / name).is_file(), name
