"""ADR-016 tesliminin Trading Home görsel sözleşmesi bekçileri."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates" / "trading_home.html").read_text(
    encoding="utf-8")
BASE = (ROOT / "templates" / "dash_base.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "trading_home_v2.css").read_text(
    encoding="utf-8")
JS = (ROOT / "static" / "js" / "trading_home.js").read_text(
    encoding="utf-8")


def test_v2_stylesheet_is_local_and_cache_busted():
    assert "css/trading_home_v2.css" in TEMPLATE
    assert "?v={{ app_version }}" in TEMPLATE
    assert "http://" not in CSS
    assert "https://" not in CSS


def test_terminal_palette_uses_cool_blue_accent():
    for token in ("--th-bg", "--th-panel", "--th-accent", "#4c8dff"):
        assert token in CSS


def test_trading_home_removes_duplicate_legacy_headers():
    assert "#exec-topbar" in CSS
    assert ".main > header" in CSS
    assert "display: none !important" in CSS


def test_primary_information_hierarchy_is_preserved():
    order = [
        TEMPLATE.index('id="th-topbar"'),
        TEMPLATE.index('id="th-accounts-strip"'),
        TEMPLATE.index('class="th-cards"'),
        TEMPLATE.index('id="th-trades"'),
        TEMPLATE.index('id="th-lists"'),
    ]
    assert order == sorted(order)


def test_secondary_diagnostics_are_progressively_disclosed():
    assert 'class="th-card th-diagnostics"' in TEMPLATE
    assert 'class="th-card th-runtime"' in TEMPLATE
    assert "Model Ayrıntıları ve Öğrenme" in TEMPLATE


def test_responsive_terminal_breakpoints_exist():
    for breakpoint in ("1260px", "900px", "760px", "480px"):
        assert breakpoint in CSS


def test_sidebar_copy_is_clean_and_professional():
    user_nav = BASE[BASE.index('<div class="nav">'):BASE.index("nav-system")]
    for emoji in ("🏡", "💼", "📈", "👁️", "♟️", "🛰️"):
        assert emoji not in user_nav


def test_adr016_status_is_visible_without_new_api():
    for element_id in ("th-ai-decision-profile", "th-ai-regime",
                       "th-ai-ranked"):
        assert f'id="{element_id}"' in TEMPLATE
    assert "function renderDecisionEngine" in JS
    assert "d.decision_engine" in JS
    for reason in ("REGIME_UNSTABLE", "STRATEGY_NOT_CONFIRMED",
                   "INSUFFICIENT_CALIBRATION", "NET_EV_NON_POSITIVE",
                   "NET_EV_CONFIDENCE_LOW",
                   "RANK_BELOW_CYCLE_CUTOFF"):
        assert reason in JS
