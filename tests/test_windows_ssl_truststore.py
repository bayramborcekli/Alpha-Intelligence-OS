"""Windows SSL truststore köprüsü — kaynak denetimi.

MERGE GUARD NOTU: Operatör onaylı; görev ajanları 'kapsam dışı' diye
kaldıramaz. SSL doğrulaması ASLA kapatılmaz.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_truststore_injected_windows_only():
    src = (ROOT / "serve_windows.py").read_text(encoding="utf-8")
    assert "truststore" in src
    assert "inject_into_ssl" in src
    # Yalnız Windows bloğu içinde
    idx = src.index("inject_into_ssl")
    assert 'if os.name == "nt":' in src[:idx]


def test_no_verify_false_anywhere():
    """verify=False / SSL doğrulaması kapatma hiçbir dosyada yok."""
    for p in [ROOT / "serve_windows.py", ROOT / "launcher_windows.py",
              *(ROOT / "alpha20_v1").glob("*.py")]:
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert "verify=False" not in text, p.name
        assert "CERT_NONE" not in text, p.name


def test_launcher_installs_truststore():
    src = (ROOT / "launcher_windows.py").read_text(encoding="utf-8")
    assert '"truststore"' in src


def test_install_path_installs_and_smoke_tests_truststore():
    """İlk kurulum (--install) yolu truststore'u açıkça kurar ve
    'import truststore' smoke testiyle doğrular."""
    src = (ROOT / "launcher_windows.py").read_text(encoding="utf-8")
    # Kurulum adimlari (steps listesi) truststore'u acikca icermeli.
    steps_idx = src.index("steps = [")
    steps_block = src[steps_idx:src.index("\n    ]", steps_idx)]
    assert '"truststore"' in steps_block, (
        "--install pip adimi truststore'u acikca kurmali")
    assert '"certifi"' in steps_block
    # Kurulum sonrasi smoke test: import truststore.
    assert "import truststore" in src, (
        "kurulum sonrasi 'import truststore' smoke testi olmali")
    # Smoke test steps blogundan SONRA gelmeli (kurulum yolu icinde).
    assert src.index("import truststore") > steps_idx


def test_requirements_has_windows_truststore():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "truststore" in req and 'sys_platform == "win32"' in req
