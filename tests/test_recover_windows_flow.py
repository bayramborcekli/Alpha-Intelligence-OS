"""RECOVER_WINDOWS.cmd entegre kurtarma akışı — ağsız kalıcı testler.

Kapsam (Task: tek tık kurtarma akışının Windows doğrulaması):
- Batch mantığı statik kontrolleri: git fallback, errorlevel kapıları,
  PowerShell filtresinin tırnak/boşluk güvenliği.
- PowerShell süreç filtresi regex davranışı (boşluklu klasör yolları,
  [regex]::Escape eşleniği ile).
- windows_diagnose --wait-health çıkış kodları + ALPHA_PORT desteği
  (ağ YOK; requests ve saat taklit edilir).

MERGE GUARD NOTU: Operatör onaylı davranış; görev ajanları 'kapsam dışı'
diye kaldıramaz.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import windows_diagnose as wd  # noqa: E402

CMD_PATH = ROOT / "RECOVER_WINDOWS.cmd"
CMD_TEXT = CMD_PATH.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 1) Batch statik kontrolleri (gerçek cmd.exe davranışına göre gözden geçirme)
# ---------------------------------------------------------------------------

class TestBatchStatic:
    def test_git_fallback_paths_present(self):
        assert r"%ProgramFiles%\Git\cmd\git.exe" in CMD_TEXT
        assert r"%ProgramFiles(x86)%\Git\cmd\git.exe" in CMD_TEXT
        assert "if defined GIT_EXE" in CMD_TEXT
        # git yoksa akış DEVAM etmeli (exit değil, uyarı)
        assert "guncelleme atlandi" in CMD_TEXT

    def test_errorlevel_gates(self):
        # Kurulum, teşhis ve health bekleme başarısızsa akış durmalı
        assert CMD_TEXT.count("if errorlevel 1") >= 4
        assert CMD_TEXT.count("exit /b 1") >= 3

    def test_ps_filter_does_not_embed_dp0(self):
        """%~dp0 PS tek tırnak içine gömülürse kesme işaretli yollar kırılır.

        Güvenli kalıp: cd /d "%~dp0" + PS'in kendi çalışma dizini.
        """
        ps_lines = [l for l in CMD_TEXT.splitlines() if "powershell" in l.lower()]
        assert ps_lines, "PowerShell süreç kapatma satırı bulunamadı"
        for line in ps_lines:
            assert "'%~dp0'" not in line, "yol PS string'ine gömülmüş (tırnak riski)"
        assert 'cd /d "%~dp0"' in CMD_TEXT
        assert "Get-Location" in CMD_TEXT
        assert "[regex]::Escape" in CMD_TEXT

    def test_ps_filter_targets_only_alpha_processes(self):
        assert "serve_windows|launcher_windows" in CMD_TEXT
        assert "Name='python.exe' or Name='pythonw.exe'" in CMD_TEXT
        # taskkill /IM python.exe gibi geniş kapsamlı öldürme OLMAMALI
        assert "taskkill" not in CMD_TEXT.lower()

    def test_wait_health_uses_120s(self):
        assert "--wait-health 120" in CMD_TEXT


# ---------------------------------------------------------------------------
# 2) PowerShell süreç filtresi regex davranışı ([regex]::Escape ≈ re.escape)
# ---------------------------------------------------------------------------

def _ps_filter_matches(root_dir: str, command_line: str) -> bool:
    """Batch'teki PS filtresinin Python eşleniği.

    $root=[regex]::Escape(<cwd> + sep); CommandLine -match ($root+'.*(serve_windows|launcher_windows)')
    """
    root = root_dir.rstrip("\\") + "\\"
    pattern = re.escape(root) + r".*(serve_windows|launcher_windows)"
    return re.search(pattern, command_line) is not None


class TestPsProcessFilter:
    ROOT_SPACED = r"C:\Users\Ali Veli\Alpha Intelligence OS"

    def test_matches_own_serve_windows_in_spaced_folder(self):
        cl = (r'"C:\Users\Ali Veli\Alpha Intelligence OS\.venv\Scripts\python.exe" '
              r'"C:\Users\Ali Veli\Alpha Intelligence OS\serve_windows.py"')
        assert _ps_filter_matches(self.ROOT_SPACED, cl)

    def test_matches_launcher_windows(self):
        cl = (r'python.exe "C:\Users\Ali Veli\Alpha Intelligence OS\launcher_windows.py"')
        assert _ps_filter_matches(self.ROOT_SPACED, cl)

    def test_does_not_match_other_folder_copy(self):
        cl = r'python.exe "C:\Other\Alpha Kopya\serve_windows.py"'
        assert not _ps_filter_matches(self.ROOT_SPACED, cl)

    def test_does_not_match_unrelated_python(self):
        cl = r'python.exe "C:\Users\Ali Veli\Alpha Intelligence OS\some_tool.py"'
        assert not _ps_filter_matches(self.ROOT_SPACED, cl)

    def test_regex_metachars_in_path_are_escaped(self):
        root = r"C:\Users\A(li)+Veli\Alpha [OS]"
        cl = root + r"\serve_windows.py"
        assert _ps_filter_matches(root, cl)
        # Escape olmasa '(li)+' regex olarak yorumlanır ve farklı yol eşleşirdi
        assert not _ps_filter_matches(root, r"C:\Users\Alili Veli\Alpha OS\serve_windows.py")

    def test_null_commandline_equivalent(self):
        # PS'te $null -match '...' False döner; Python eşleniği: boş string
        assert not _ps_filter_matches(self.ROOT_SPACED, "")


# ---------------------------------------------------------------------------
# 3) --wait-health çıkış kodları + ALPHA_PORT (ağsız)
# ---------------------------------------------------------------------------

class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    return clock


def _patch_requests_get(monkeypatch, fn):
    import requests
    monkeypatch.setattr(requests, "get", fn)


class TestWaitHealth:
    def test_server_never_responds_returns_1(self, monkeypatch, fake_clock, capsys):
        def get(url, timeout=5):
            raise ConnectionError("refused")
        _patch_requests_get(monkeypatch, get)
        rc = wd.wait_health(timeout_s=30)
        out = capsys.readouterr().out
        assert rc == 1
        assert "STOPPED" in out

    def test_controller_running_with_cycle_returns_0(self, monkeypatch, fake_clock, capsys):
        payload = {"controller": "running", "cycle_count": 3,
                   "runtime_override": True, "paper": True,
                   "auto_loop": True, "entrypoint": "serve_windows"}
        _patch_requests_get(monkeypatch, lambda url, timeout=5: _Resp(200, payload))
        rc = wd.wait_health(timeout_s=30)
        out = capsys.readouterr().out
        assert rc == 0
        assert "RUNNING" in out

    def test_running_but_no_cycle_returns_1(self, monkeypatch, fake_clock, capsys):
        payload = {"controller": "running", "cycle_count": 0,
                   "runtime_override": True}
        _patch_requests_get(monkeypatch, lambda url, timeout=5: _Resp(200, payload))
        rc = wd.wait_health(timeout_s=15)
        out = capsys.readouterr().out
        assert rc == 1
        assert "ROOT CAUSE" in out

    def test_override_false_root_cause(self, monkeypatch, fake_clock, capsys):
        payload = {"controller": "stopped", "cycle_count": 0,
                   "runtime_override": False}
        _patch_requests_get(monkeypatch, lambda url, timeout=5: _Resp(200, payload))
        rc = wd.wait_health(timeout_s=15)
        out = capsys.readouterr().out
        assert rc == 1
        assert "runtime_override=false" in out

    def test_alpha_port_respected(self, monkeypatch, fake_clock):
        monkeypatch.setenv("ALPHA_PORT", "7777")
        seen: list[str] = []

        def get(url, timeout=5):
            seen.append(url)
            return _Resp(200, {"controller": "running", "cycle_count": 1})
        _patch_requests_get(monkeypatch, get)
        assert wd.wait_health(timeout_s=10) == 0
        assert seen and all(":7777/" in u for u in seen)

    def test_alpha_port_blank_falls_back_to_5000(self, monkeypatch, fake_clock):
        monkeypatch.setenv("ALPHA_PORT", "   ")
        seen: list[str] = []

        def get(url, timeout=5):
            seen.append(url)
            return _Resp(200, {"controller": "running", "cycle_count": 1})
        _patch_requests_get(monkeypatch, get)
        assert wd.wait_health(timeout_s=10) == 0
        assert seen and all(":5000/" in u for u in seen)

    def test_late_recovery_within_deadline(self, monkeypatch, fake_clock):
        calls = {"n": 0}

        def get(url, timeout=5):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("not up yet")
            return _Resp(200, {"controller": "running", "cycle_count": 1})
        _patch_requests_get(monkeypatch, get)
        assert wd.wait_health(timeout_s=60) == 0


class TestWaitHealthArgParsing:
    def test_no_flag_returns_none(self):
        assert wd.parse_wait_health_timeout(["windows_diagnose.py"]) is None

    def test_valid_number(self):
        assert wd.parse_wait_health_timeout(["x", "--wait-health", "120"]) == 120

    def test_missing_number_defaults(self):
        assert wd.parse_wait_health_timeout(["x", "--wait-health"]) == 120

    def test_garbage_number_defaults(self):
        assert wd.parse_wait_health_timeout(["x", "--wait-health", "abc"]) == 120

    def test_negative_clamped_to_zero(self):
        assert wd.parse_wait_health_timeout(["x", "--wait-health", "-5"]) == 0
