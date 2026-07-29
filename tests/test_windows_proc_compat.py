"""Windows /proc uyumluluğu — HTTP 500 kök neden düzeltmesi.

Kanıtlanan: find_bot_pids() Windows'ta (/proc yokken) FileNotFoundError
fırlatmaz; /api/v1/executive/summary 200 döner; Linux davranışı değişmez.

MERGE GUARD NOTU: Operatör onaylı davranış; görev ajanları 'kapsam dışı'
diye kaldıramaz.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path as _RealPath

import pytest

ROOT = _RealPath(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


class _FakeProcMissing:
    """Path('/proc') taklidi: yok sayılır (Windows davranışı)."""
    def exists(self):
        return False

    def iterdir(self):  # asla çağrılmamalı
        raise FileNotFoundError("[WinError 3] C:\\proc")


class _FakeProcOSError:
    def exists(self):
        return True

    def iterdir(self):
        raise OSError("beklenmeyen OS hatası")


def _fake_path_factory(fake_proc):
    def factory(arg, *a, **k):
        if str(arg) == "/proc":
            return fake_proc
        return _RealPath(arg, *a, **k)
    return factory


class _OsNtProxy:
    """os taklidi: yalnız name='nt'; geri kalanı gerçek os'a delege.

    os.name'i global patch'lemek pathlib'i WindowsPath'e zorlar ve Linux'ta
    NotImplementedError üretir; bu proxy yalnız app modülünün görüşünü
    değiştirir."""
    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


def test_windows_os_name_nt_returns_empty(monkeypatch):
    """A) os.name == 'nt' → tarama yok, boş liste, exception yok."""
    monkeypatch.setattr(app_module, "os", _OsNtProxy())
    assert app_module.find_bot_pids() == []
    assert app_module.bot_running() is False


def test_missing_proc_returns_empty(monkeypatch):
    """A) /proc yok (Windows) → FileNotFoundError OLUŞMAZ, boş liste."""
    monkeypatch.setattr(app_module, "Path",
                        _fake_path_factory(_FakeProcMissing()))
    assert app_module.find_bot_pids() == []


def test_unexpected_oserror_is_defended(monkeypatch, caplog):
    """Savunma katmanı: iterdir OSError verirse warning + boş liste."""
    monkeypatch.setattr(app_module, "Path",
                        _fake_path_factory(_FakeProcOSError()))
    with caplog.at_level("WARNING"):
        assert app_module.find_bot_pids() == []
    assert any("PID taraması" in r.message for r in caplog.records)


def test_linux_behavior_unchanged():
    """E) Linux/Replit: /proc taraması aynen çalışır, kendi PID'i hariç."""
    assert _RealPath("/proc").exists(), "Bu test Linux ortamında koşar"
    pids = app_module.find_bot_pids()
    assert isinstance(pids, list)
    assert os.getpid() not in pids


def test_windows_bot_running_via_pidfile(monkeypatch, tmp_path):
    """C) Windows'ta bot_running() PID dosyası + canlılık ile durum döner."""
    monkeypatch.setattr(app_module, "os", _OsNtProxy())
    pidfile = tmp_path / ".bot.pid"
    monkeypatch.setattr(app_module, "PID_PATH", pidfile)
    monkeypatch.setattr(app_module, "BOT_OUTPUT", tmp_path / "bot_process.log")
    # PID dosyası yok → çalışmıyor
    assert app_module.bot_running() is False
    # PID dosyası var + süreç canlı sayılıyor → çalışıyor
    pidfile.write_text('{"pid": 12345}', encoding="utf-8")
    monkeypatch.setattr(app_module, "_pid_alive", lambda pid: pid == 12345)
    assert app_module.bot_running() is True
    # Süreç ölü → çalışmıyor
    monkeypatch.setattr(app_module, "_pid_alive", lambda pid: False)
    assert app_module.bot_running() is False


class TestCrashDetection:
    """Task: bot çökünce panel yanlış 'çalışıyor' göstermesin.

    Ölü PID tespit edilir edilmez .bot.pid silinir ve çöküş
    bot_process.log'a WARNING olarak yazılır.
    """

    def _setup(self, monkeypatch, tmp_path, alive=False):
        monkeypatch.setattr(app_module, "os", _OsNtProxy())
        pidfile = tmp_path / ".bot.pid"
        botlog = tmp_path / "bot_process.log"
        monkeypatch.setattr(app_module, "PID_PATH", pidfile)
        monkeypatch.setattr(app_module, "BOT_OUTPUT", botlog)
        pidfile.write_text('{"pid": 12345}', encoding="utf-8")
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: alive)
        return pidfile, botlog

    def test_dead_pid_removes_pidfile_and_logs(self, monkeypatch, tmp_path):
        pidfile, botlog = self._setup(monkeypatch, tmp_path, alive=False)
        assert app_module.bot_running() is False
        assert not pidfile.exists(), ".bot.pid anında silinmeli"
        content = botlog.read_text(encoding="utf-8")
        assert "BOT ÇÖKTÜ" in content
        assert "PID=12345" in content
        assert "WARNING" in content

    def test_crash_logged_only_once(self, monkeypatch, tmp_path):
        pidfile, botlog = self._setup(monkeypatch, tmp_path, alive=False)
        assert app_module.bot_running() is False
        assert app_module.bot_running() is False  # ikinci anket
        content = botlog.read_text(encoding="utf-8")
        assert content.count("BOT ÇÖKTÜ") == 1, "tek çöküş tek uyarı"

    def test_alive_pid_untouched(self, monkeypatch, tmp_path):
        pidfile, botlog = self._setup(monkeypatch, tmp_path, alive=True)
        assert app_module.bot_running() is True
        assert pidfile.exists()
        assert not botlog.exists()

    def test_linux_stale_pidfile_cleaned(self, monkeypatch, tmp_path):
        """Linux yolu: /proc taraması bot bulamazsa bayat .bot.pid temizlenir."""
        pidfile = tmp_path / ".bot.pid"
        botlog = tmp_path / "bot_process.log"
        monkeypatch.setattr(app_module, "PID_PATH", pidfile)
        monkeypatch.setattr(app_module, "BOT_OUTPUT", botlog)
        monkeypatch.setattr(app_module, "find_bot_pids", lambda: [])
        pidfile.write_text('{"pid": 12345}', encoding="utf-8")
        assert app_module.bot_running() is False
        assert not pidfile.exists()
        assert "BOT ÇÖKTÜ" in botlog.read_text(encoding="utf-8")

    def test_status_endpoint_reflects_crash(self, monkeypatch, tmp_path):
        """Panelin okuduğu build_status() çöküş sonrası running=False döner."""
        self._setup(monkeypatch, tmp_path, alive=False)
        status, _ = app_module.build_status()
        assert status["running"] is False


def test_windows_stop_bot_uses_pid_alive(monkeypatch, tmp_path):
    """C) Windows'ta stop_bot cmdline yerine PID dosyası doğrulaması yapar."""
    monkeypatch.setattr(app_module, "os", _OsNtProxy())
    pidfile = tmp_path / ".bot.pid"
    monkeypatch.setattr(app_module, "PID_PATH", pidfile)
    # Bot yok → net mesaj, exception yok
    ok, msg = app_module.stop_bot()
    assert ok is False
    assert "bulunamadı" in msg


def test_linux_pid_alive_matches_reality():
    """E) Linux: _pid_alive kendi PID'i için True, imkânsız PID için False."""
    assert app_module._pid_alive(os.getpid()) is True
    assert app_module._pid_alive(2**22 + 12345) is False
    assert app_module._pid_alive(0) is False

class _FakeBotProc:
    def __init__(self, pid):
        self.pid = pid
def test_executive_summary_returns_200_on_windows_sim(monkeypatch):
    """B) Windows simülasyonunda /api/v1/executive/summary → HTTP 200."""
    monkeypatch.setattr(app_module, "os", _OsNtProxy())
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as s:
            s["logged_in"] = True
            s["username"] = "test"
        r = c.get("/api/v1/executive/summary")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert "status_bar" in body  # D) servis durumları gerçek değerlerle döner

class _SubprocessNtProxy:
    """subprocess taklidi: Windows bayrakları var; Popen kwargs'ı yakalar."""
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008

    def __init__(self):
        self.calls = []

    def Popen(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeBotProc(pid=42424)

    def __getattr__(self, attr):
        import subprocess as _real
        return getattr(_real, attr)

def test_windows_start_bot_uses_detached_flags(monkeypatch, tmp_path):
    """F) Windows'ta start_bot DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    bayraklarını KULLANMALI ve POSIX'e özgü start_new_session'ı KULLANMAMALI.

    Bu bayraklar panel süreci kapansa da botun yaşamasını sağlar; kaldırılması
    sessiz bir regresyondur (manuel protokol adım 6)."""
    fake_sub = _SubprocessNtProxy()
    bot_path = tmp_path / "alpha20.py"
    bot_path.write_text("pass", encoding="utf-8")
    monkeypatch.setattr(app_module, "os", _OsNtProxy())
    monkeypatch.setattr(app_module, "subprocess", fake_sub)
    monkeypatch.setattr(app_module, "BOT_PATH", bot_path)
    monkeypatch.setattr(app_module, "PID_PATH", tmp_path / ".bot.pid")
    monkeypatch.setattr(app_module, "BOT_OUTPUT", tmp_path / "bot.log")

    ok, msg = app_module.start_bot()
    assert ok is True, msg
    assert len(fake_sub.calls) == 1
    _args, kwargs = fake_sub.calls[0]
    expected = (_SubprocessNtProxy.CREATE_NEW_PROCESS_GROUP
                | _SubprocessNtProxy.DETACHED_PROCESS)
    assert kwargs.get("creationflags") == expected
    assert "start_new_session" not in kwargs
    # PID dosyası yazıldı → panel yeniden açıldığında botu bulabilir
    assert app_module.read_pid() == 42424

def test_bot_survives_parent_death(tmp_path):
    """F) Gerçek süreç testi: botu başlatan 'panel' süreci ölür, bot
    _pid_alive kalır. (Linux'ta start_new_session=True aynı garantiyi
    sağlar; Windows'taki DETACHED_PROCESS davranışının POSIX karşılığı.)"""
    import subprocess as real_subprocess

    bot = tmp_path / "fake_bot.py"
    bot.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    pidfile = tmp_path / ".bot.pid"

    script = _PARENT_LAUNCHER.format(
        root=str(ROOT), bot=str(bot), pidfile=str(pidfile),
        out=str(tmp_path / "bot.log"))
    # 'Panel' süreci: botu başlatır ve hemen ölür (crash/kapanma simülasyonu).
    result = real_subprocess.run(
        [sys.executable, "-c", script], capture_output=True,
        text=True, timeout=60)
    assert result.returncode == 0, (
        f"panel süreci başarısız: {result.stdout} {result.stderr}")

    # Panel öldü — bot PID'i dosyadan okunur ve hâlâ canlı olmalı.
    pid = json.loads(pidfile.read_text(encoding="utf-8"))["pid"]
    try:
        assert app_module._pid_alive(pid) is True, (
            "Bot, panel süreci öldükten sonra yaşamıyor — detach regresyonu!")
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
