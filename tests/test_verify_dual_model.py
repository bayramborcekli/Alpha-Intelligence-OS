# -*- coding: utf-8 -*-
"""verify_dual_model.py saha aracının CI birim testleri (Task 136).

Saha aracı Windows'ta elle koşturulur; ama çekirdek mantık
(liste kontrolü, koşu-kapsamlı işlem kontrolü, phase-pre/post
restart korunumu, git temizliği, 429 kanıtı, HTTP istemcisi)
mock HTTP sunucu + mock git ile burada otomatik doğrulanır.
"""
import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "windows" / "verify_dual_model.py"

_spec = importlib.util.spec_from_file_location("verify_dual_model", TOOL)
vdm = importlib.util.module_from_spec(_spec)
sys.modules["verify_dual_model"] = vdm
_spec.loader.exec_module(vdm)


# ── Durum üreticileri ─────────────────────────────────────────────

def make_state(core=("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"),
               opp=("DOGEUSDT",), positions=(), trades=(),
               core_closed=0, opp_closed=0, total_open=None,
               live_orders="DISABLED"):
    positions = list(positions)
    return {
        "core_list": [{"symbol": s, "spread_pct": 0.01} for s in core],
        "opportunity_list": [{"symbol": s} for s in opp],
        "positions": positions,
        "recent_trades": list(trades),
        "live_orders": live_orders,
        "counters": {
            "total_open": (len(positions) if total_open is None
                           else total_open),
            "core_open": 0, "opportunity_open": 0,
        },
        "metrics": {
            "ALPHA_CORE_SCALP": {"closed_positions": core_closed},
            "ALPHA_OPPORTUNITY_BURST": {"closed_positions": opp_closed},
        },
        "last_refresh": "2026-07-30T00:00:00Z",
        "last_error": None,
    }


def pos(symbol="BTCUSDT", model="ALPHA_CORE_SCALP",
        entry=100.0, opened_at="2026-07-30T00:00:00Z"):
    return {"symbol": symbol, "model": model, "entry": entry,
            "opened_at": opened_at, "quantity": 1.0, "side": "BUY"}


# ── check_lists ───────────────────────────────────────────────────

class TestCheckLists:
    def test_pass_with_pinned_and_opportunity(self):
        assert vdm.check_lists(make_state()) == []

    def test_fail_missing_pinned(self):
        s = make_state(core=("BTCUSDT", "ETHUSDT", "BNBUSDT"))
        fails = vdm.check_lists(s)
        assert any("SOLUSDT" in f for f in fails)

    def test_fail_core_too_small(self):
        s = make_state(core=("BTCUSDT",))
        fails = vdm.check_lists(s)
        assert any("CORE listesi dolu değil" in f for f in fails)

    def test_fail_empty_opportunity(self):
        s = make_state(opp=())
        fails = vdm.check_lists(s)
        assert any("OPPORTUNITY listesi BOŞ" in f for f in fails)

    def test_fail_live_orders_not_disabled(self):
        s = make_state(live_orders="ENABLED")
        fails = vdm.check_lists(s)
        assert any("live_orders" in f for f in fails)

    def test_string_symbol_lists_accepted(self):
        s = make_state()
        s["core_list"] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        s["opportunity_list"] = ["DOGEUSDT"]
        assert vdm.check_lists(s) == []


# ── check_trades_scoped ───────────────────────────────────────────

class TestCheckTradesScoped:
    def test_pass_open_and_close_this_run(self):
        s = make_state(positions=[pos()], core_closed=3,
                       trades=[{"symbol": "ETHUSDT",
                                "model": "ALPHA_CORE_SCALP",
                                "net_pnl": 1.0, "result": "WIN",
                                "closed_at": "x"}])
        assert vdm.check_trades_scoped(s, baseline_closed=2) == []

    def test_fail_nothing_opened(self):
        s = make_state(positions=(), core_closed=5)
        fails = vdm.check_trades_scoped(s, baseline_closed=5)
        assert any("hiç Paper pozisyon açılmadı" in f for f in fails)

    def test_fail_no_close_this_run(self):
        # Tarihi kapanışlar PASS'a sayılmaz: baseline == now.
        s = make_state(positions=[pos()], core_closed=7)
        fails = vdm.check_trades_scoped(s, baseline_closed=7)
        assert any("hiç işlem KAPANMADI" in f for f in fails)
        assert not any("açılmadı" in f for f in fails)

    def test_close_without_open_position_counts_as_opened(self):
        s = make_state(positions=(), core_closed=3)
        assert vdm.check_trades_scoped(s, baseline_closed=2) == []


# ── phase_pre + phase_post (restart korunumu) ─────────────────────

@pytest.fixture()
def pre_snapshot(tmp_path, monkeypatch):
    snap = tmp_path / "verify_dual_model_pre.json"
    monkeypatch.setattr(vdm, "PRE_SNAPSHOT", snap)
    return snap


class TestPhasePrePost:
    def test_pre_writes_snapshot(self, pre_snapshot):
        s = make_state(positions=[pos()])
        assert vdm.phase_pre(s) == []
        data = json.loads(pre_snapshot.read_text(encoding="utf-8"))
        assert data["summary"]["core_list"]
        assert data["summary"]["open_position_ids"]

    def test_post_without_pre_fails(self, pre_snapshot):
        fails = vdm.phase_post(make_state())
        assert any("--phase pre" in f for f in fails)

    def test_post_identical_state_passes(self, pre_snapshot):
        s = make_state(positions=[pos(), pos("ETHUSDT")], core_closed=2)
        vdm.phase_pre(s)
        assert vdm.phase_post(s) == []

    def test_regression_position_lost_fails(self, pre_snapshot):
        """Restart sonrası pozisyon ne açık ne trades'de → FAIL."""
        before = make_state(positions=[pos("BTCUSDT"), pos("ETHUSDT")])
        vdm.phase_pre(before)
        after = make_state(positions=[pos("ETHUSDT")])  # BTC kayıp
        fails = vdm.phase_post(after)
        assert any("pozisyon kimliği KAYBOLDU" in f for f in fails)
        assert any("BTCUSDT" in f for f in fails)

    def test_position_closed_into_trades_is_not_lost(self, pre_snapshot):
        before = make_state(positions=[pos("BTCUSDT")])
        vdm.phase_pre(before)
        after = make_state(
            positions=(), core_closed=1,
            trades=[{"symbol": "BTCUSDT", "model": "ALPHA_CORE_SCALP",
                     "net_pnl": 0.5, "result": "WIN", "closed_at": "x"}])
        assert vdm.phase_post(after) == []

    def test_reopened_with_new_identity_fails(self, pre_snapshot):
        before = make_state(positions=[pos("BTCUSDT", entry=100.0,
                                           opened_at="t1")])
        vdm.phase_pre(before)
        # Sembol trades'de göründüğü için "kayıp" sayılmaz; ama aynı
        # sembol farklı entry/opened_at ile tekrar açıksa kimlik
        # korunumu ihlali FAIL üretmeli.
        after = make_state(
            positions=[pos("BTCUSDT", entry=105.0, opened_at="t2")],
            trades=[{"symbol": "BTCUSDT"}])
        fails = vdm.phase_post(after)
        assert any("farklı kimlikle" in f for f in fails)
        # trades'de de yoksa: düpedüz kayıp → yine FAIL
        after2 = make_state(
            positions=[pos("BTCUSDT", entry=105.0, opened_at="t2")])
        fails2 = vdm.phase_post(after2)
        assert any("KAYBOLDU" in f for f in fails2)

    def test_lists_emptied_after_restart_fails(self, pre_snapshot):
        before = make_state()
        vdm.phase_pre(before)
        after = make_state(core=(), opp=())
        fails = vdm.phase_post(after)
        assert any("core_list restart sonrası BOŞALDI" in f
                   for f in fails)
        assert any("opportunity_list restart sonrası BOŞALDI" in f
                   for f in fails)

    def test_closed_trades_decrease_means_reset(self, pre_snapshot):
        before = make_state(core_closed=5)
        vdm.phase_pre(before)
        after = make_state(core_closed=1)
        fails = vdm.phase_post(after)
        assert any("runtime store sıfırlanmış" in f for f in fails)


# ── check_git_clean (mock git deposu) ─────────────────────────────

@pytest.fixture()
def git_repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config",
                    "user.name", "t"], check=True)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"],
                   check=True)
    monkeypatch.setattr(vdm, "REPO", tmp_path)
    return tmp_path


class TestGitClean:
    def test_clean_tree_passes(self, git_repo):
        assert vdm.check_git_clean() == []

    def test_dirty_tree_fails(self, git_repo):
        (git_repo / "runtime_leak.json").write_text("{}")
        fails = vdm.check_git_clean()
        assert any("git status temiz değil" in f for f in fails)

    def test_own_report_files_are_ignored(self, git_repo):
        (git_repo / "verify_dual_model_report.json").write_text("{}")
        (git_repo / "verify_dual_model_pre.json").write_text("{}")
        assert vdm.check_git_clean() == []


# ── check_backoff_evidence ────────────────────────────────────────

class TestBackoffEvidence:
    def test_no_rate_state_is_not_applicable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vdm, "RATE_STATE",
                            tmp_path / "rate_limit_state.json")
        assert vdm.check_backoff_evidence() == []

    def test_rate_state_with_log_trace_passes(self, tmp_path, monkeypatch):
        rs = tmp_path / "rate_limit_state.json"
        rs.write_text(json.dumps({"until": 123}), encoding="utf-8")
        log = tmp_path / "alpha20.log"
        log.write_text("2026-07-30 WARN 429 geri çekilme aktif\n",
                       encoding="utf-8")
        monkeypatch.setattr(vdm, "RATE_STATE", rs)
        monkeypatch.setattr(vdm, "LOG_CANDIDATES", (log,))
        assert vdm.check_backoff_evidence() == []

    def test_rate_state_without_log_trace_fails(self, tmp_path,
                                                monkeypatch):
        rs = tmp_path / "rate_limit_state.json"
        rs.write_text(json.dumps({"until": 123}), encoding="utf-8")
        log = tmp_path / "alpha20.log"
        log.write_text("2026-07-30 INFO her şey yolunda\n",
                       encoding="utf-8")
        monkeypatch.setattr(vdm, "RATE_STATE", rs)
        monkeypatch.setattr(vdm, "LOG_CANDIDATES", (log,))
        fails = vdm.check_backoff_evidence()
        assert any("geri çekilme izi bulunamadı" in f for f in fails)

    def test_unreadable_rate_state_fails(self, tmp_path, monkeypatch):
        rs = tmp_path / "rate_limit_state.json"
        rs.write_text("BOZUK{{", encoding="utf-8")
        monkeypatch.setattr(vdm, "RATE_STATE", rs)
        fails = vdm.check_backoff_evidence()
        assert any("okunamadı" in f for f in fails)


# ── Mock HTTP sunucusu: Client / login / get_state ────────────────

class _Handler(BaseHTTPRequestHandler):
    state = make_state()
    require_login = True

    def log_message(self, *a):  # sessiz
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if getattr(self, "_set_cookie", False):
            self.send_header("Set-Cookie", "session=ok; Path=/")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/login":
            self._send(200,
                       '<form><input name="csrf_token" value="tok123">'
                       "</form>", "text/html")
        elif self.path == "/api/dual-model/state":
            cookies = self.headers.get("Cookie", "")
            if self.require_login and "session=ok" not in cookies:
                self._send(401, '{"ok": false}')
            else:
                self._send(200, json.dumps(
                    {"ok": True, "data": type(self).state}))
        else:
            self._send(404, "{}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        if self.path == "/login" and "csrf_token=tok123" in body:
            self._set_cookie = True
            self._send(200, "ok", "text/html")
        else:
            self._send(400, "bad", "text/html")


@pytest.fixture()
def mock_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


class TestHttpClient:
    def test_login_and_get_state(self, mock_server, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "operator")
        monkeypatch.setattr(vdm.getpass, "getpass", lambda *a: "pw")
        c = vdm.Client(mock_server)
        vdm.login(c)
        s = vdm.get_state(c)
        assert vdm._list_symbols(s["core_list"])[:1] == ["BTCUSDT"]
        assert vdm.check_lists(s) == []

    def test_get_state_ok_false_raises(self, mock_server, monkeypatch):
        class NotOk(_Handler):
            def do_GET(self):
                self._send(200, '{"ok": false, "error": "x"}')
        srv = ThreadingHTTPServer(("127.0.0.1", 0), NotOk)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            c = vdm.Client(f"http://127.0.0.1:{srv.server_address[1]}")
            with pytest.raises(RuntimeError, match="ok=False"):
                vdm.get_state(c)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_login_unreachable_raises(self):
        c = vdm.Client("http://127.0.0.1:1")
        with pytest.raises((RuntimeError, OSError)):
            vdm.login(c)


# ── summarize determinizmi ────────────────────────────────────────

class TestSummarize:
    def test_summary_is_deterministic_and_complete(self):
        s = make_state(positions=[pos("ETHUSDT"), pos("BTCUSDT")],
                       core_closed=2, opp_closed=3)
        a, b = vdm.summarize(s), vdm.summarize(s)
        assert a == b
        assert a["closed_trades"] == 5
        assert a["open_position_ids"] == sorted(a["open_position_ids"])

    def test_positions_dict_form_supported(self):
        s = make_state()
        s["positions"] = {"BTCUSDT": pos("BTCUSDT")}
        assert vdm.summarize(s)["open_position_ids"][0][0] == "BTCUSDT"
