"""
Alpha Intelligence OS — Merkezi sürüm bilgisi.

Sürüm bilgisini buradan okuyun; asla sabit kodlamayın.
Dashboard'da yalnızca sürüm numarası gösterilebilir;
build hash veya altyapı ayrıntıları gösterilmemelidir.
"""
from __future__ import annotations

import pathlib

_VERSION_FILE = pathlib.Path(__file__).parent / "VERSION"


def get_version() -> str:
    """VERSION dosyasından sürümü döndür (strip edilmiş)."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


# Modül düzeyinde kullanım kolaylığı için
__version__: str = get_version()

# Semantic Versioning alanları (MAJOR.MINOR.PATCH[-PRE])
def _parse_version(v: str) -> dict:
    pre = None
    if "-" in v:
        core, pre = v.split("-", 1)
    else:
        core = v
    parts = core.split(".")
    return {
        "major": int(parts[0]) if len(parts) > 0 else 0,
        "minor": int(parts[1]) if len(parts) > 1 else 0,
        "patch": int(parts[2]) if len(parts) > 2 else 0,
        "pre":   pre,
        "full":  v,
    }


VERSION_INFO: dict = _parse_version(__version__)
