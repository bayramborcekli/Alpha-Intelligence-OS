"""Görev #83 — Windows runtime override görünürlüğü.

Kanıtlanan: auto_controller.get_status() bellek-içi RUNTIME_ADAPTIVE_OVERRIDE
aktifken salt-okunur `runtime_override` bilgisini döndürür; /api/adaptive/status
bunu dışa verir; panelde rozet yalnız override aktifken görünür (Linux/Replit
varsayılanında hiçbir görsel değişiklik yok).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "alpha20_v1"))

import auto_controller as ac  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_override():
    saved = dict(ac.RUNTIME_ADAPTIVE_OVERRIDE)
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()
    yield
    ac.RUNTIME_ADAPTIVE_OVERRIDE.clear()
    ac.RUNTIME_ADAPTIVE_OVERRIDE.update(saved)


def test_status_default_no_override():
    st = ac.get_status()
    assert st["runtime_override"] is False
    assert "runtime_override_flags" not in st


def test_status_reports_active_override():
    ac.set_runtime_adaptive_override({"enabled": True, "mode": "AUTO",
                                      "auto_paper_enabled": True})
    st = ac.get_status()
    assert st["runtime_override"] is True
    assert st["runtime_override_flags"]["mode"] == "AUTO"
    # Salt-okunur: status kopyasını değiştirmek gerçek override'ı bozmaz
    st["runtime_override_flags"]["mode"] = "HACK"
    assert ac.RUNTIME_ADAPTIVE_OVERRIDE["mode"] == "AUTO"


def test_api_adaptive_status_exposes_override():
    import app as app_module
    ac.set_runtime_adaptive_override({"enabled": True, "mode": "AUTO"})
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "test"
            from datetime import datetime, timezone
            sess["login_time"] = datetime.now(timezone.utc).isoformat()
        r = c.get("/api/adaptive/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data["controller"]["runtime_override"] is True


def test_dashboard_template_badge_only_when_override():
    """Şablon: rozet ve uyarı yalnız ctrl.runtime_override koşuluna bağlı;
    override yokken (Linux/Replit) hiçbir görsel değişiklik yok."""
    import app as app_module
    # Şablon derlenebilir olmalı
    app_module.app.jinja_env.get_template("dashboard.html")
    src = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "RUNTIME OVERRIDE (yalnız bellek)" in src
    assert "Geçici Bellek Modu" in src
    # Rozet ve uyarı yalnız runtime_override koşullu bloklar içinde
    for marker in ("RUNTIME OVERRIDE (yalnız bellek)", "Geçici Bellek Modu"):
        idx = src.index(marker)
        cond = src.rfind("adaptive.ctrl.runtime_override", 0, idx)
        assert cond != -1 and idx - cond < 400, (
            f"'{marker}' runtime_override koşulundan bağımsız görünüyor")
