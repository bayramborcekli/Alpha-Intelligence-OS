"""Windows tek-tık başlatıcı testleri (Mission 2400 → 2500 paketleme).

Windows'a özgü davranış burada (Linux) koşulamaz; bu testler
(1) POSIX tarafında davranışın DEĞİŞMEDİĞİNİ (portable_flock gerçek
fcntl'e vekâlet eder), (2) launcher_windows.py + cmd betiklerinin
sözleşmelerini (bootstrap, .venv izolasyonu, kopya koruması, güvenli
durdurma, secret sızıntısı yok) statik ve birim düzeyde kilitler.
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
INSTALL_CMD = (ROOT / "INSTALL_WINDOWS.cmd").read_text(encoding="utf-8")
LAUNCHER_PY = (ROOT / "launcher_windows.py").read_text(encoding="utf-8")
SHORTCUT_PS1 = (ROOT / "tools/windows/create_desktop_shortcut.ps1"
                ).read_text(encoding="utf-8")
SERVE_PY = (ROOT / "serve_windows.py").read_text(encoding="utf-8")
ALL_SCRIPTS = (START_CMD + STOP_CMD + INSTALL_CMD + LAUNCHER_PY +
               SHORTCUT_PS1 + SERVE_PY)


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
        "alpha20_v1/auto_controller.py",
        "alpha20_v1/strategy_lab.py"])
    def test_fallback_import_pattern(self, module):
        src = (ROOT / module).read_text(encoding="utf-8")
        assert "import fcntl" in src
        assert "import portable_flock as fcntl" in src
        assert src.index("import fcntl") < src.index("portable_flock")

    def test_modules_use_real_fcntl_here(self):
        import accounts_registry
        assert accounts_registry.fcntl is real_fcntl


class TestCmdContracts:
    def test_start_cmd_uses_own_folder_as_root(self):
        assert 'cd /d "%~dp0"' in START_CMD

    def test_start_cmd_prefers_venv_python(self):
        assert ".venv\\Scripts\\python.exe" in START_CMD
        # .venv varsa sistem python'a HİÇ düşülmez (guard önce gelir)
        assert START_CMD.index(".venv\\Scripts\\python.exe") < \
            START_CMD.index("where py")

    def test_start_cmd_no_silent_failure(self):
        assert "pause" in START_CMD
        assert "errorlevel" in START_CMD

    def test_no_hardcoded_paths_anywhere(self):
        for banned in ("D:\\PROGRAMLAR", "C:\\Users", "D:\\Alpha",
                       "GTHUPNEW"):
            assert banned not in ALL_SCRIPTS, banned

    def test_stop_cmd_delegates_to_launcher(self):
        assert "launcher_windows.py" in STOP_CMD
        assert "--stop" in STOP_CMD

    def test_install_cmd_delegates_to_launcher(self):
        assert "launcher_windows.py" in INSTALL_CMD
        assert "--install" in INSTALL_CMD
        assert "pause" in INSTALL_CMD


class TestLauncherContract:
    def test_root_resolved_from_file_not_cwd(self):
        assert "Path(__file__).resolve().parent" in LAUNCHER_PY

    def test_venv_python_only_no_system_fallback_for_runtime(self):
        import launcher_windows as lw
        assert str(lw.venv_python()).startswith(str(lw.ROOT))

    def test_readiness_checked_before_browser(self):
        # Tarayıcı yalnız /health hazırlık denetiminden SONRA açılır.
        ready = LAUNCHER_PY.index("Hazirlik denetimi OK")
        browser = LAUNCHER_PY.index("open_browser(HOME_URL)", ready)
        assert browser > ready

    def test_duplicate_start_uses_live_health_check(self):
        assert "alpha_healthy()" in LAUNCHER_PY
        assert "kopya baslatilmadi" in LAUNCHER_PY

    def test_foreign_port_user_gets_clear_error(self):
        assert "baska uygulama tarafindan kullaniliyor" in LAUNCHER_PY

    def test_never_kills_foreign_process(self):
        # taskkill yalnız stop() içinde ve üç kimlik denetiminden sonra.
        assert "pid_belongs_to_this_root" in LAUNCHER_PY
        assert "DOKUNULMADI" in LAUNCHER_PY

    def test_no_path_mutation(self):
        assert "setx" not in ALL_SCRIPTS.lower()
        assert 'os.environ["PATH"]' not in LAUNCHER_PY

    def test_pid_file_in_runtime_dir(self):
        import launcher_windows as lw
        assert lw.PID_FILE == lw.ROOT / "runtime" / "alpha.pid"

    def test_localhost_only_server(self):
        assert '"127.0.0.1"' in SERVE_PY
        assert "0.0.0.0" not in SERVE_PY

    def test_start_cmd_delegates_to_launcher(self):
        assert "launcher_windows.py" in START_CMD


class TestStopSafety:
    def test_stop_requires_identity_before_kill(self):
        import launcher_windows as lw
        src = LAUNCHER_PY[LAUNCHER_PY.index("def stop"):]
        kill = src.index("taskkill")
        for guard in ("pid_alive", "pid_belongs_to_this_root",
                      'meta.get("root") != str(ROOT)'):
            assert src.index(guard) < kill, guard

    def test_no_broad_python_kill(self):
        assert "/IM" not in LAUNCHER_PY
        assert "Get-Process python" not in ALL_SCRIPTS

    def test_stale_pid_cleanup(self, tmp_path, monkeypatch):
        import launcher_windows as lw
        monkeypatch.setattr(lw, "RUNTIME", tmp_path)
        monkeypatch.setattr(lw, "PID_FILE", tmp_path / "alpha.pid")
        (tmp_path / "alpha.pid").write_text(
            '{"pid": 999999, "root": "%s"}' % lw.ROOT)
        monkeypatch.setattr(lw, "alpha_healthy", lambda: False)
        assert lw.stop() == 0
        assert not (tmp_path / "alpha.pid").exists()

    def test_stop_refuses_other_clone_pid(self, tmp_path, monkeypatch):
        import launcher_windows as lw
        monkeypatch.setattr(lw, "PID_FILE", tmp_path / "alpha.pid")
        (tmp_path / "alpha.pid").write_text(
            '{"pid": 1, "root": "/eski/clone"}')
        assert lw.stop() != 0
        assert (tmp_path / "alpha.pid").exists()  # DOKUNULMADI


class TestShortcut:
    def test_shortcut_targets_current_clone(self):
        assert "$PSScriptRoot" in SHORTCUT_PS1
        assert "start_alpha.cmd" in SHORTCUT_PS1
        assert "WorkingDirectory = $ProjectRoot" in SHORTCUT_PS1

    def test_shortcut_no_hardcoded_paths(self):
        for banned in ("D:\\", "C:\\Users"):
            assert banned not in SHORTCUT_PS1


class TestNoSecretLeakage:
    @pytest.mark.parametrize("secret", [
        "BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_TR_API_KEY",
        "BINANCE_TR_API_SECRET", "SESSION_SECRET",
        "ALPHA_OWNER_PASSWORD_HASH"])
    def test_cmd_scripts_never_reference_secrets(self, secret):
        assert secret not in (START_CMD + STOP_CMD + INSTALL_CMD +
                              SHORTCUT_PS1 + LAUNCHER_PY)

    def test_serve_logs_metadata_not_values(self):
        # serve_windows yalnız present/source/length loglar.
        assert "credential_metadata" in SERVE_PY
        assert 'meta["present"]' in SERVE_PY

    def test_launcher_log_fixed_messages_only(self):
        import ast
        tree = ast.parse(LAUNCHER_PY)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "log"):
                src = ast.unparse(node)
                assert "environ" not in src, src


class TestLinuxProductionPathUntouched:
    def test_gunicorn_config_unchanged_reference(self):
        cfg = (ROOT / "gunicorn.conf.py").read_text(encoding="utf-8")
        assert "workers = 2" in cfg
        assert 'bind = "0.0.0.0:5000"' in cfg

    def test_waitress_declared(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        assert "waitress" in req

    def test_gitignore_protects_local_state(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert ".venv/" in gi
        assert "runtime/" in gi
        assert "\n.env\n" in gi
