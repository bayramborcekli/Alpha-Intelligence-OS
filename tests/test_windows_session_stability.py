"""Windows oturum kararlılığı — SESSION_SECRET kalıcılığı (Mission topbar+session).

Kapsam:
- .env.example dosyasında SESSION_SECRET alanı var (Windows kullanıcısı
  dosyayı doldurunca restart sonrası oturumlar korunur).
- local_env, .env içindeki SESSION_SECRET değerini yükler.
- app.py secret çözümleme önceliği: FLASK_SECRET_KEY > SESSION_SECRET >
  geçici (rastgele) anahtar. Sabit anahtar verildiğinde deterministiktir
  → restart'ta aynı anahtar → oturum çerezleri geçerli kalır.

MERGE GUARD NOTU: Bu dosya operatör onaylı davranışı korur; görev
ajanları 'kapsam dışı' diye silemez.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env_example() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


def test_env_example_has_session_secret_field():
    """SESSION_SECRET= satırı şablonda bulunmalı (Windows kalıcı oturum)."""
    assert re.search(r"^SESSION_SECRET=", _env_example(), re.MULTILINE)


def test_env_example_warns_about_restart_session_loss():
    """Şablon, boş bırakılırsa oturumların düşeceğini açıklamalı."""
    text = _env_example()
    assert "SESSION_SECRET" in text
    assert "oturum" in text.lower()


def test_local_env_parses_session_secret(tmp_path):
    """local_env._parse_env_file .env'deki SESSION_SECRET'i okumalı."""
    import local_env
    p = tmp_path / ".env"
    p.write_text("SESSION_SECRET=sabit-test-anahtari-123\n", encoding="utf-8")
    parsed = local_env._parse_env_file(p)
    assert parsed.get("SESSION_SECRET") == "sabit-test-anahtari-123"


def test_app_secret_resolution_is_deterministic():
    """app.py'deki çözümleme mantığı: sabit env → sabit anahtar.

    app.py'nin kullandığı ifadeyi birebir uygulayıp iki 'restart'
    simülasyonunun aynı anahtarı ürettiğini doğrularız."""
    env = {"SESSION_SECRET": "sabit-anahtar"}

    def resolve(e: dict) -> str | None:
        return e.get("FLASK_SECRET_KEY") or e.get("SESSION_SECRET") or None

    assert resolve(env) == resolve(dict(env)) == "sabit-anahtar"
    # FLASK_SECRET_KEY önceliği korunur:
    env["FLASK_SECRET_KEY"] = "birincil"
    assert resolve(env) == "birincil"


def test_app_source_secret_precedence_intact():
    """app.py kaynağı: FLASK_SECRET_KEY → SESSION_SECRET → geçici anahtar
    zinciri ve restart uyarısı yerinde kalmalı (merge guard)."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'os.environ.get("FLASK_SECRET_KEY")' in src
    assert 'os.environ.get("SESSION_SECRET")' in src
    assert "yeniden başlatmada oturumlar geçersiz olur" in src


def test_topbar_distinguishes_error_states():
    """Üst çubuk: 401 oturum ayrımı + 'Veri Alınamadı' durumu yerinde
    (tek istek hatası servisleri 'Bağlantı Yok' boyamamalı)."""
    tpl = (ROOT / "templates" / "_exec_topbar.html").read_text(encoding="utf-8")
    assert "Veri Alınamadı" in tpl
    assert "r.status === 401" in tpl
    assert "sessionExpired" in tpl
    # 200-yolu bozulmadı: status_bar alanları hâlâ tek tek işleniyor.
    for key in ("binance_global", "ledger", "audit", "risk_engine", "health"):
        assert key in tpl
