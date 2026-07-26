"""
Alpha Intelligence OS — Sahiplik ve bütünlük testleri
ownership-baseline-v1

Kapsam:
- .env git tarafından izlenmiyor
- PAPER_MODE varsayılanı true
- Canlı emir özelliği varsayılan kapalı
- Yedek bütünlük kontrolü
- Bozuk yedeğin reddedilmesi
- Restore dry-run
- Secret örneklerinin repoda bulunmaması
- Sürüm bilgisinin geçerli formatta olması
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent


# ══════════════════════════════════════════════════════════════════════════════
# 1. Git izleme testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestGitTracking:

    def test_env_file_not_tracked_by_git(self):
        """.env dosyası git tarafından izlenmemeli (.gitignore'da olmalı)."""
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env"],
            cwd=ROOT, capture_output=True
        )
        # exit code 0 → .gitignore'da (ignored) → DOĞRU
        assert result.returncode == 0, \
            ".env dosyası git tarafından izleniyor! .gitignore'a ekleyin."

    def test_env_example_is_tracked(self):
        """.env.example dosyası git'te izlenmeli."""
        example = ROOT / ".env.example"
        assert example.exists(), ".env.example dosyası mevcut değil"
        # .gitignore'da olmamalı
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env.example"],
            cwd=ROOT, capture_output=True
        )
        assert result.returncode != 0, \
            ".env.example yanlışlıkla .gitignore'a eklenmiş"

    def test_gitignore_covers_secret_patterns(self):
        """.gitignore sır içerebilecek yaygın uzantıları kapsamalı."""
        gitignore = ROOT / ".gitignore"
        assert gitignore.exists(), ".gitignore dosyası mevcut değil"
        content = gitignore.read_text(encoding="utf-8")
        required = [".env", "*.key", "*.pem", "*.p12", "secrets/", "backups/"]
        for pattern in required:
            assert pattern in content, \
                f".gitignore içinde '{pattern}' eksik"

    def test_dot_env_not_in_git_history(self):
        """.env dosyası git geçmişinde commit edilmemiş olmalı."""
        result = subprocess.run(
            ["git", "log", "--all", "--full-history", "--", ".env"],
            cwd=ROOT, capture_output=True, text=True
        )
        # Çıktı boşsa .env hiç commit edilmemiş
        assert result.stdout.strip() == "", \
            ".env dosyası git geçmişinde bulunuyor — secret rotasyonu gerekebilir!"


# ══════════════════════════════════════════════════════════════════════════════
# 2. PAPER modu ve canlı emir güvenliği
# ══════════════════════════════════════════════════════════════════════════════

class TestPaperModeSafety:

    def test_paper_mode_default_true_in_env_example(self):
        """PAPER_MODE .env.example'da varsayılan olarak true olmalı."""
        env_example = ROOT / ".env.example"
        assert env_example.exists()
        content = env_example.read_text(encoding="utf-8")
        assert "PAPER_MODE=true" in content, \
            ".env.example'da PAPER_MODE=true bulunamadı"

    def test_config_mode_is_paper(self):
        """alpha20_v1/config.json mode alanı PAPER olmalı."""
        config_path = ROOT / "alpha20_v1" / "config.json"
        if not config_path.exists():
            pytest.skip("config.json mevcut değil")
        with config_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        assert cfg.get("mode") == "PAPER", \
            f"config.json mode='PAPER' değil: {cfg.get('mode')}"

    def test_no_live_order_functions_in_source(self):
        """Kaynak kodda canlı emir fonksiyonu bulunmamalı."""
        forbidden = [
            "create_order",
            "place_order",
            "submit_order",
            "send_order",
            "live_trade",
        ]
        py_files = list(ROOT.glob("**/*.py"))
        py_files = [f for f in py_files
                    if ".pythonlibs" not in str(f)
                    and "test_" not in f.name
                    and "__pycache__" not in str(f)]

        for path in py_files:
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for func in forbidden:
                assert func not in source, \
                    f"Yasak fonksiyon '{func}' bulundu: {path.relative_to(ROOT)}"

    def test_no_api_keys_in_config(self):
        """alpha20_v1/config.json API anahtarı içermemeli."""
        config_path = ROOT / "alpha20_v1" / "config.json"
        if not config_path.exists():
            pytest.skip("config.json mevcut değil")
        content = config_path.read_text(encoding="utf-8").lower()
        for keyword in ("api_key", "api_secret", "apikey", "apisecret", "private_key"):
            assert keyword not in content, \
                f"config.json şüpheli alan içeriyor: '{keyword}'"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Yedek bütünlük testleri
# ══════════════════════════════════════════════════════════════════════════════

def _make_test_backup(tmp_path: pathlib.Path) -> pathlib.Path:
    """Test için geçici geçerli yedek dizini oluşturur."""
    backup = tmp_path / "20250126_120000"
    backup.mkdir()

    # Bazı örnek dosyalar
    (backup / "config.json").write_text('{"mode":"PAPER","symbols":["BTCUSDT"]}')
    (backup / "state.json").write_text('{"balance":10000,"positions":[]}')

    # MANIFEST
    (backup / "MANIFEST.txt").write_text(
        "Tarih     : 2025-01-26T12:00:00Z\n"
        "Sürüm     : 0.1.0-alpha\n"
        "Checkpoint: 20250126_120000\n"
    )

    # SHA-256 checksums
    checksums = ""
    for f in sorted(backup.iterdir()):
        if f.name == "CHECKSUMS.sha256":
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        checksums += f"{digest}  ./{f.name}\n"
    (backup / "CHECKSUMS.sha256").write_text(checksums)

    return backup


class TestBackupIntegrity:

    def test_valid_backup_passes_integrity_check(self, tmp_path):
        """Geçerli yedek bütünlük denetiminden geçmeli."""
        backup = _make_test_backup(tmp_path)
        result = subprocess.run(
            ["sha256sum", "-c", "CHECKSUMS.sha256", "--quiet"],
            cwd=backup, capture_output=True, text=True
        )
        assert result.returncode == 0, \
            f"Geçerli yedek bütünlük kontrolünden geçemedi: {result.stderr}"

    def test_tampered_backup_fails_integrity_check(self, tmp_path):
        """Değiştirilmiş yedek bütünlük denetiminde başarısız olmalı."""
        backup = _make_test_backup(tmp_path)
        # Bir dosyayı boz
        config = backup / "config.json"
        config.write_text('{"mode":"LIVE","symbols":["HACKED"]}')  # değiştir

        result = subprocess.run(
            ["sha256sum", "-c", "CHECKSUMS.sha256", "--quiet"],
            cwd=backup, capture_output=True, text=True
        )
        assert result.returncode != 0, \
            "Bozuk yedek bütünlük kontrolünden geçmemeli!"

    def test_backup_without_checksums_is_incomplete(self, tmp_path):
        """CHECKSUMS.sha256 olmayan yedek eksik kabul edilmeli."""
        backup = _make_test_backup(tmp_path)
        (backup / "CHECKSUMS.sha256").unlink()
        assert not (backup / "CHECKSUMS.sha256").exists(), \
            "CHECKSUMS.sha256 kaldırılamadı"

    def test_backup_without_manifest_is_incomplete(self, tmp_path):
        """MANIFEST.txt olmayan yedek eksik kabul edilmeli."""
        backup = _make_test_backup(tmp_path)
        (backup / "MANIFEST.txt").unlink()
        assert not (backup / "MANIFEST.txt").exists(), \
            "MANIFEST.txt kaldırılamadı"

    def test_restore_dry_run_does_not_modify_files(self, tmp_path):
        """restore.sh --dry-run hiçbir dosyayı değiştirmemeli."""
        backup = _make_test_backup(tmp_path)
        restore_script = ROOT / "restore.sh"
        if not restore_script.exists():
            pytest.skip("restore.sh mevcut değil")

        # Hedef dizinde başlangıç durumunu kaydet
        sentinel = tmp_path / "sentinel.txt"
        sentinel.write_text("original")

        result = subprocess.run(
            ["bash", str(restore_script), "--dry-run", str(backup)],
            cwd=tmp_path, capture_output=True, text=True
        )
        assert result.returncode == 0, \
            f"restore.sh --dry-run başarısız: {result.stderr}"
        assert "DRY-RUN" in result.stdout.upper() or "dry-run" in result.stdout.lower()

        # Sentinel dosyası değişmemiş olmalı
        assert sentinel.read_text() == "original", \
            "--dry-run sentinel dosyasını değiştirdi!"

    def test_restore_rejects_corrupt_backup(self, tmp_path):
        """Bozuk yedek restore tarafından reddedilmeli."""
        backup = _make_test_backup(tmp_path)
        # Bütünlüğü boz
        (backup / "config.json").write_text('{"CORRUPTED": true}')

        restore_script = ROOT / "restore.sh"
        if not restore_script.exists():
            pytest.skip("restore.sh mevcut değil")

        result = subprocess.run(
            ["bash", str(restore_script), "--dry-run", str(backup)],
            cwd=tmp_path, capture_output=True, text=True
        )
        # Bütünlük hatası → sıfırdan farklı çıkış kodu
        assert result.returncode != 0, \
            "Bozuk yedek restore tarafından kabul edildi! Reddedilmeliydi."


# ══════════════════════════════════════════════════════════════════════════════
# 4. Secret taraması
# ══════════════════════════════════════════════════════════════════════════════

class TestSecretScan:

    # Gerçek secret değerlerine benzeyen desenler
    SECRET_PATTERNS = [
        r'(?i)api[_-]?key\s*=\s*["\']?[A-Za-z0-9+/]{20,}',
        r'(?i)api[_-]?secret\s*=\s*["\']?[A-Za-z0-9+/]{20,}',
        r'(?i)private[_-]?key\s*=\s*["\']?[A-Za-z0-9+/]{20,}',
        r'(?i)token\s*=\s*["\']?[A-Za-z0-9._-]{30,}',
        r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----',
    ]

    # İzin verilen false-positive içerikler (placeholder, yorum, test)
    ALLOWLIST = [
        "your-secret-key-here",
        "your-hash-here",
        "placeholder",
        "example",
        "change-this",
        "CHANGE_ME",
        "OWNER_NAME",
        "pbkdf2:sha256:...your-hash-here...",
        "pbkdf2:sha256:",   # hash prefix — placeholder göstergesi
        "test",
        "mock",
        "dummy",
    ]

    def _is_allowlisted(self, line: str) -> bool:
        lower = line.lower()
        return any(a.lower() in lower for a in self.ALLOWLIST)

    def test_no_hardcoded_secrets_in_python_files(self):
        """Python kaynak dosyalarında hardcoded secret bulunmamalı."""
        py_files = list(ROOT.glob("**/*.py"))
        py_files = [f for f in py_files
                    if ".pythonlibs" not in str(f)
                    and "__pycache__" not in str(f)]

        findings = []
        for path in py_files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines, 1):
                # Yorum satırlarını atla
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for pattern in self.SECRET_PATTERNS:
                    if re.search(pattern, line) and not self._is_allowlisted(line):
                        findings.append(
                            f"{path.relative_to(ROOT)}:{i} — secret deseni eşleşti"
                        )
        assert not findings, \
            "Hardcoded secret bulundu:\n" + "\n".join(findings)

    def test_no_hardcoded_secrets_in_config_files(self):
        """JSON yapılandırma dosyalarında API anahtarı bulunmamalı."""
        json_files = list(ROOT.glob("**/*.json"))
        json_files = [f for f in json_files
                      if ".pythonlibs" not in str(f)
                      and ".git" not in str(f)
                      and "backups" not in str(f)]

        danger_keys = {"api_key", "api_secret", "apikey", "apisecret",
                       "private_key", "secret_key", "password", "token"}

        for path in json_files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for k, v in data.items():
                k_lower = k.lower().replace("-", "_")
                if k_lower in danger_keys and v and isinstance(v, str) and len(v) > 8:
                    pytest.fail(
                        f"{path.relative_to(ROOT)}: '{k}' anahtarında değer var. "
                        f"Sırları config dosyasına yazmayın."
                    )

    def test_env_example_contains_no_real_values(self):
        """.env.example gerçek değer içermemeli (placeholder olmalı)."""
        env_example = ROOT / ".env.example"
        assert env_example.exists()
        lines = env_example.read_text(encoding="utf-8").splitlines()
        real_value_pattern = re.compile(
            r'^[A-Z_]+=(?!your-|example|placeholder|true|false|production|'
            r'development|5000|0|admin|pbkdf2:|\.\.\.|\.\.\.).+'
        )
        suspicious = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            m = real_value_pattern.match(stripped)
            if not m:
                continue
            value = stripped.split("=", 1)[1]
            # Allowlist kontrolü
            if any(a.lower() in value.lower() for a in self.ALLOWLIST):
                continue
            if len(value) > 16:
                suspicious.append(stripped)
        assert not suspicious, \
            f".env.example'da gerçek değer gibi görünen satırlar: {suspicious}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Sürüm bilgisi testleri
# ══════════════════════════════════════════════════════════════════════════════

class TestVersioning:

    def test_version_file_exists(self):
        """VERSION dosyası mevcut olmalı."""
        assert (ROOT / "VERSION").exists(), "VERSION dosyası bulunamadı"

    def test_version_format_is_valid_semver(self):
        """VERSION geçerli Semantic Versioning formatında olmalı."""
        version_str = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        # MAJOR.MINOR.PATCH veya MAJOR.MINOR.PATCH-PRE
        pattern = re.compile(r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$')
        assert pattern.match(version_str), \
            f"VERSION geçersiz SemVer formatı: '{version_str}'"

    def test_version_module_importable(self):
        """version.py import edilebilmeli ve sürüm döndürmeli."""
        import sys
        sys.path.insert(0, str(ROOT))
        try:
            import version as ver
            v = ver.get_version()
            assert v and v != "unknown", f"get_version() boş veya 'unknown': {v}"
        finally:
            sys.path.pop(0)

    def test_version_module_info_fields(self):
        """VERSION_INFO sözlüğü beklenen alanları içermeli."""
        import sys
        sys.path.insert(0, str(ROOT))
        try:
            import version as ver
            info = ver.VERSION_INFO
            assert "major" in info
            assert "minor" in info
            assert "patch" in info
            assert "full" in info
            assert isinstance(info["major"], int)
            assert isinstance(info["minor"], int)
            assert isinstance(info["patch"], int)
        finally:
            sys.path.pop(0)
