"""Mission 2400 — Agent 01: Windows tek-tık başlatıcı testleri.

Windows'a özgü davranış burada (Linux) koşulamaz; bu testler
(1) POSIX tarafında davranışın DEĞİŞMEDİĞİNİ (portable_flock gerçek
fcntl'e vekâlet eder), (2) başlatıcı betiklerinin sözleşmelerini
(hazırlık denetimi tarayıcıdan önce, kopya koruması, güvenli durdurma,
gizli değer sızıntısı yok) statik olarak kilitler.
"""
from __future__ import annotations

import fcntl as real_fcntl
import subprocess
import tempfile
from pathlib import Path

import pytest

import portable_flock

ROOT = Path(__file__).resolve().parent.parent
START_CMD = (ROOT / "start_alpha.cmd").read_text(encoding="utf-8")
STOP_CMD = (ROOT / "stop_alpha.cmd").read_text(encoding="utf-8")
LAUNCH_PS1 = (ROOT / "tools/windows/launch_alpha.ps1").read_text(encoding="utf-8")
STOP_PS1 = (ROOT / "tools/windows/stop_alpha.ps1").read_text(encoding="utf-8")
SHORTCUT_PS1 = (ROOT / "tools/windows/create_shortcuts.ps1").read_text(encoding="utf-8")
SERVE_PY = (ROOT / "serve_windows.py").read_text(encoding="utf-8")
ALL_SCRIPTS = START_CMD + STOP_CMD + LAUNCH_PS1 + STOP_PS1 + SHORTCUT_PS1 + SERVE_PY


class TestPortableFlockPosix:
    """Linux'ta portable_flock gerçek fcntl'e birebir vekâlettir."""

    def test_constants_match_real_fcntl(self):
        assert portable_flock.LOCK_EX == real_fcntl.LOCK_EX
        assert portable_flock.LOCK_NB == real_fcntl.LOCK_NB
        assert portable_flock.LOCK_UN == real_fcntl.LOCK_UN

    def test_flock_roundtrip(self):
        with tempfile.NamedTemporaryFile() as fh:
            portable_flock.flock(fh, portable_flock.LOCK_EX)
            portable_flock.flock(fh, portable_flock.LOCK_UN)

    def test_nonblocking_conflict_raises(self):
        with tempfile.NamedTemporaryFile() as fh:
            portable_flock.flock(
                fh, portable_flock.LOCK_EX | portable_flock.LOCK_NB)
            # Ayrı süreçte aynı dosyaya NB kilit denemesi başarısız olmalı.
            code = subprocess.run(
                ["python", "-c",
                 "import sys, portable_flock as p;"
                 "fh=open(sys.argv[1],'a');"
                 "\ntry:\n p.flock(fh, p.LOCK_EX | p.LOCK_NB)\n"
                 "except OSError:\n sys.exit(42)\nsys.exit(0)",
                 fh.name],
                cwd=ROOT).returncode
            assert code == 42


class TestSharedStateModulesUnchangedOnPosix:
    """Paylaşımlı durum modülleri Linux'ta GERÇEK fcntl kullanır."""

    @pytest.mark.parametrize("module", [
        "accounts_registry.py", "operation_control_store.py",
        "automation_engine.py", "intelligence_timeline.py",
        "alpha20_v1/auto_controller.py"])
    def test_fallback_import_pattern(self, module):
        src = (ROOT / module).read_text(encoding="utf-8")
        assert "import fcntl" in src
        assert "import portable_flock as fcntl" in src
        # Gerçek fcntl önce denenir (try bloğu fallback'ten önce).
        assert src.index("import fcntl") < src.index("portable_flock")

    def test_modules_use_real_fcntl_here(self):
        import accounts_registry
        assert accounts_registry.fcntl is real_fcntl


class TestLauncherContract:
    def test_readiness_checked_before_browser(self):
        # Tarayıcı yalnız /health hazırlık denetiminden SONRA açılır.
        assert "/health" in LAUNCH_PS1
        ready_ok = LAUNCH_PS1.index('Write-Log "Hazirlik denetimi OK')
        browser = LAUNCH_PS1.index("Start-Process $HomeUrl", ready_ok)
        assert browser > ready_ok

    def test_trading_home_route_used(self):
        assert "/home" in LAUNCH_PS1  # gerçek rota, varsayım değil

    def test_duplicate_start_prevented_by_live_check(self):
        # Bayat PID'e değil canlı /health denetimine dayanır.
        assert "if (Test-Ready)" in LAUNCH_PS1
        assert "kopya baslatilmadi" in LAUNCH_PS1

    def test_failure_does_not_open_broken_page(self):
        fail_branch = LAUNCH_PS1[LAUNCH_PS1.index("if (-not $Ready)"):]
        assert "Start-Process $HomeUrl" not in fail_branch.split("Write-Log \"Hazirlik denetimi OK")[0]

    def test_no_fixed_drive_assumption(self):
        for banned in ("C:\\\\", "C:\\Users", "D:\\Alpha"):
            assert banned not in LAUNCH_PS1

    def test_localhost_only_server(self):
        assert '"127.0.0.1"' in SERVE_PY
        assert "0.0.0.0" not in SERVE_PY

    def test_start_cmd_delegates_to_launcher(self):
        assert "launch_alpha.ps1" in START_CMD


class TestStopSafety:
    def test_no_broad_python_kill(self):
        # İlgisiz python süreçleri asla hedeflenmez.
        assert "/IM" not in STOP_PS1
        assert "Get-Process python" not in STOP_PS1
        assert "taskkill /PID $AppPid" in STOP_PS1

    def test_pid_identity_triple_check(self):
        # PID yeniden kullanımına karşı üçlü kimlik: pid + başlama
        # zamanı (start_ticks) + BU projenin serve_windows.py TAM yolu.
        assert "start_ticks" in LAUNCH_PS1  # başlatıcı meta yazar
        assert "start_ticks" in STOP_PS1    # durdurucu doğrular
        assert "StartTime.Ticks" in STOP_PS1
        assert "[regex]::Escape($ExpectedScript)" in STOP_PS1
        assert "DOKUNULMADI" in STOP_PS1

    def test_mismatch_refuses_before_any_kill(self):
        # Tüm kimlik denetimleri ilk taskkill'den ÖNCE gelir.
        kill = STOP_PS1.index("taskkill /PID")
        for guard in ("start_ticks", "StartTime.Ticks",
                      "[regex]::Escape($ExpectedScript)"):
            assert STOP_PS1.index(guard) < kill, guard

    def test_lock_removed_only_after_shutdown(self):
        kill = STOP_PS1.index("taskkill /PID")
        cleanup = STOP_PS1.rindex("Remove-Item $PidFile")
        assert cleanup > kill


class TestNoSecretLeakage:
    @pytest.mark.parametrize("secret", [
        "BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_TR_API_KEY",
        "BINANCE_TR_API_SECRET", "SESSION_SECRET",
        "ALPHA_OWNER_PASSWORD_HASH"])
    def test_scripts_never_reference_secret_values(self, secret):
        # Başlatıcı gizli değişkenleri okumaz, loglamaz, argüman yapmaz.
        assert secret not in ALL_SCRIPTS

    def test_log_writes_only_fixed_messages(self):
        # Write-Log çağrılarında değişken enterpolasyonlu ortam içeriği yok.
        for line in (LAUNCH_PS1 + STOP_PS1).splitlines():
            if "Write-Log" in line and "$env:" in line:
                pytest.fail(f"launcher log ortam içeriği yazıyor: {line}")


class TestLinuxProductionPathUntouched:
    def test_gunicorn_config_unchanged_reference(self):
        cfg = (ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")
        assert "workers = 2" in cfg
        assert 'bind = "0.0.0.0:5000"' in cfg

    def test_waitress_declared(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        assert "waitress" in req
