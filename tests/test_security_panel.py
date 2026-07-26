"""
tests/test_security_panel.py — Güvenlik paneli sızıntı testleri (Görev 24).
security_log.get_security_summary düşmanca / bozuk log satırlarını güvenle
işlemeli ve panel HTML'i asla parola benzeri içerik göstermemeli.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Log satırlarına gömülen nöbetçi (sentinel) değerler — HTML'de asla görünmemeli
SENTINELS = [
    "hunter2SECRETVALUE",
    "sup3rs3cr3tpw",
    "leak-me-token-abc123",
]


def _ts(minutes_ago: int = 0) -> str:
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _line(minutes_ago: int, rest: str) -> str:
    return f"{_ts(minutes_ago)}Z | {rest}"


@pytest.fixture
def sec_log(tmp_path, monkeypatch):
    """slog.LOG_PATH'i geçici bir dosyaya yönlendir ve yazıcı fonksiyon döndür."""
    import security_log as slog
    path = tmp_path / "security.log"
    monkeypatch.setattr(slog, "LOG_PATH", path)
    def write(lines: list[str]) -> None:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return write


# ══════════════════════════════════════════════════════════════════════════════
# get_security_summary — bozuk / düşmanca satırlar
# ══════════════════════════════════════════════════════════════════════════════

class TestSummaryHostileInput:

    def test_missing_file_returns_empty_summary(self, tmp_path, monkeypatch):
        import security_log as slog
        monkeypatch.setattr(slog, "LOG_PATH", tmp_path / "yok.log")
        s = slog.get_security_summary()
        assert s == {"fail_count": 0, "locked_ip_count": 0,
                     "last_lockout": None, "recent": []}

    def test_malformed_lines_are_skipped(self, sec_log):
        import security_log as slog
        sec_log([
            "tamamen bozuk satır",
            "2026-99-99T99:99:99Z | event=LOGIN_FAIL | ip=1.1.1.1",  # geçersiz tarih
            "| | | =====",
            "\x00\x01binary\x02junk",
            _line(5, "event=LOGIN_FAIL | ip=2.2.2.2 | detail=bad creds"),
        ])
        s = slog.get_security_summary()
        assert s["fail_count"] == 1
        assert s["recent"][0]["ip"] == "2.2.2.2"

    def test_sensitive_words_never_surface_in_summary(self, sec_log):
        import security_log as slog
        sec_log([
            _line(3, f"event=LOGIN_FAIL | user=password={SENTINELS[0]} | "
                     f"ip=3.3.3.3 | detail=password={SENTINELS[1]}"),
            _line(2, f"event=LOGIN_FAIL | ip=4.4.4.4 | detail=api_key={SENTINELS[2]}"),
        ])
        s = slog.get_security_summary()
        flat = repr(s)
        for sentinel in SENTINELS:
            assert sentinel not in flat
        assert "password" not in flat
        assert s["fail_count"] == 2

    def test_pipe_injection_in_username_does_not_leak(self, sec_log):
        import security_log as slog
        sec_log([
            _line(1, f"event=LOGIN_FAIL | user=evil | detail=password={SENTINELS[0]} | ip=5.5.5.5"),
        ])
        s = slog.get_security_summary()
        assert SENTINELS[0] not in repr(s)

    def test_log_event_sanitizes_hostile_username_and_ip(self, tmp_path):
        import logging
        import security_log as slog
        test_log = tmp_path / "s.log"
        handler = logging.FileHandler(str(test_log), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        slog._logger.addHandler(handler)
        try:
            slog.log_event(slog.LOGIN_FAIL,
                           username=f"password={SENTINELS[0]}",
                           ip=f"9.9.9.9 | detail=token={SENTINELS[1]}",
                           detail="bad | creds\ninjected=line")
            handler.flush()
        finally:
            slog._logger.removeHandler(handler)
            handler.close()
        content = test_log.read_text(encoding="utf-8")
        for sentinel in SENTINELS[:2]:
            assert sentinel not in content
        assert "[REDACTED]" in content
        # Detail'deki pipe/satır sonu enjeksiyonu etkisiz olmalı
        assert "\ninjected=line" not in content
        assert "| creds" not in content


# ══════════════════════════════════════════════════════════════════════════════
# get_security_summary — zaman penceresi ve limitler
# ══════════════════════════════════════════════════════════════════════════════

class TestSummaryWindows:

    def test_events_older_than_window_excluded(self, sec_log):
        import security_log as slog
        sec_log([
            _line(60 * 30, "event=LOGIN_FAIL | ip=7.7.7.7 | detail=eski"),   # 30 saat önce
            _line(10,      "event=LOGIN_FAIL | ip=8.8.8.8 | detail=yeni"),
        ])
        s = slog.get_security_summary(hours=24)
        assert s["fail_count"] == 1
        assert s["recent"][0]["ip"] == "8.8.8.8"

    def test_narrow_window_boundary(self, sec_log):
        import security_log as slog
        sec_log([
            _line(90, "event=LOGIN_FAIL | ip=1.1.1.1 | detail=x"),  # penceredışı (1s)
            _line(30, "event=LOGIN_FAIL | ip=2.2.2.2 | detail=x"),
        ])
        s = slog.get_security_summary(hours=1)
        assert s["fail_count"] == 1

    def test_max_events_cap_and_ordering(self, sec_log):
        import security_log as slog
        sec_log([_line(20 - i, f"event=LOGIN_FAIL | ip=10.0.0.{i} | detail=x")
                 for i in range(15)])
        s = slog.get_security_summary(max_events=10)
        assert s["fail_count"] == 15
        assert len(s["recent"]) == 10
        # En yeni olay ilk sırada
        assert s["recent"][0]["ip"] == "10.0.0.14"

    def test_lockout_detection(self, sec_log):
        import security_log as slog
        sec_log([
            _line(5, "event=LOGIN_FAIL | ip=6.6.6.6 | detail=rate limited: too many attempts"),
            _line(4, "event=LOGIN_FAIL | ip=6.6.6.6 | detail=rate limited again"),
            _line(3, "event=LOGIN_FAIL | ip=7.7.7.7 | detail=bad creds"),
        ])
        s = slog.get_security_summary()
        assert s["locked_ip_count"] == 1
        assert s["last_lockout"] is not None
        assert s["recent"][0]["lockout"] is False

    def test_out_of_order_timestamps_do_not_hide_recent_events(self, sec_log):
        """Pencere dışı (eski) bir satır dosyanın sonlarına yakınsa,
        ondan ÖNCE gelen yeni olaylar yine de sayılmalı (break yerine skip)."""
        import security_log as slog
        sec_log([
            _line(5,       "event=LOGIN_FAIL | ip=1.1.1.1 | detail=yeni-1"),
            _line(60 * 48, "event=LOGIN_FAIL | ip=9.9.9.9 | detail=cok-eski"),
            _line(3,       "event=LOGIN_FAIL | ip=2.2.2.2 | detail=rate limited yeni-2"),
            _line(60 * 30, "event=LOGIN_FAIL | ip=8.8.8.8 | detail=eski"),
            _line(1,       "event=LOGIN_FAIL | ip=3.3.3.3 | detail=yeni-3"),
        ])
        s = slog.get_security_summary(hours=24)
        assert s["fail_count"] == 3
        ips = {e["ip"] for e in s["recent"]}
        assert ips == {"1.1.1.1", "2.2.2.2", "3.3.3.3"}
        assert s["locked_ip_count"] == 1
        assert s["last_lockout"] is not None

    def test_non_login_fail_events_ignored(self, sec_log):
        import security_log as slog
        sec_log([
            _line(2, "event=LOGIN_OK | user=alice | ip=1.2.3.4"),
            _line(1, "event=BOT_START | detail=x"),
        ])
        s = slog.get_security_summary()
        assert s["fail_count"] == 0
        assert s["recent"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Panel HTML — yasaklı kelime sızıntısı yok
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardHtml:

    @pytest.fixture
    def client(self):
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as c:
            yield c

    def test_dashboard_html_contains_no_forbidden_content(self, sec_log, client):
        sec_log([
            _line(3, f"event=LOGIN_FAIL | user=password={SENTINELS[0]} | "
                     f"ip=11.11.11.11 | detail=passwd={SENTINELS[1]}"),
            _line(2, f"event=LOGIN_FAIL | ip=12.12.12.12 | detail=token={SENTINELS[2]}"),
            _line(1, "event=LOGIN_FAIL | ip=12.12.12.12 | detail=rate limited"),
            "bozuk satır <script>alert(1)</script>",
        ])
        resp = client.get("/panel")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="replace")
        for sentinel in SENTINELS:
            assert sentinel not in html
        for word in ("password=", "passwd=", "token=", "secret=", "api_key="):
            assert word not in html.lower()
        assert "<script>alert(1)</script>" not in html
        # Panel gerçek verileri göstermeli
        assert "11.11.11.11" in html
        assert "12.12.12.12" in html

    def test_dashboard_renders_with_corrupt_log(self, sec_log, client):
        sec_log(["\x00garbage", "===", "no timestamp | event=LOGIN_FAIL"])
        resp = client.get("/panel")
        assert resp.status_code == 200
