"""Tek environment yükleyici — deterministik öncelik kuralı.

Kural:
- Replit / production: process environment (Replit Secrets) KAZANIR;
  proje `.env` dosyası hiç okunmaz.
- Windows / yerel geliştirme: proje kökündeki `.env` dosyası okunur.
  * Binance TR credential anahtarları için `.env` AÇIKÇA kaynaktır:
    eski/stale user- veya system-level environment değerlerinin sessizce
    öne geçmesi ENGELLENİR (override).
  * Diğer anahtarlar için process env korunur (setdefault).

Yükleme idempotenttir: `load_project_env()` kaç kez çağrılırsa çağrılsın
`.env` en fazla BİR kez uygulanır (app.py + serve_windows.py çifte yükleme
yapamaz).

Güvenlik: hiçbir credential DEĞERİ loglanmaz/döndürülmez; yalnız güvenli
metadata (source, present, length, ascii) raporlanır.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

# `.env`'in stale OS env'i açıkça geçersiz kılabildiği anahtarlar
# (yalnız salt-okunur exchange credential'ları).
OVERRIDE_KEYS = ("BINANCE_TR_API_KEY", "BINANCE_TR_API_SECRET",
                 "BINANCE_API_KEY", "BINANCE_API_SECRET",
                 "BINANCE_GLOBAL_API_KEY", "BINANCE_GLOBAL_API_SECRET",
                 "BINANCE_GLOBAL_API_Key", "BINANCE_GLOBAL_Secret_Key",
                 "BINANCE_API_Key", "BINANCE_Secret_Key")

_loaded = False
_sources: dict[str, str] = {}


def is_replit() -> bool:
    """Replit/production ortamı tespiti (process env kazanır)."""
    return ("REPL_ID" in os.environ
            or "REPLIT_DEV_DOMAIN" in os.environ
            or os.environ.get("FLASK_ENV") == "production")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Basit KEY=VALUE ayrıştırıcı (yorum ve boş satırlar atlanır).

    Windows dayanıklılığı: Notepad UTF-8 dosyayı BOM ile kaydeder
    (ilk anahtar '\\ufeffKEY' olur ve eşleşmez); PowerShell Set-Content
    varsayılanı UTF-16'dır (utf-8 okuma UnicodeDecodeError fırlatır).
    Bu iki durum GLOBAL=False/TR=False belirtisinin kök nedenidir —
    utf-8-sig önce denenir, çözülemezse utf-16'ya düşülür."""
    out: dict[str, str] = {}
    try:
        raw = path.read_bytes()
    except OSError:
        return out
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-16")
        except UnicodeError:
            return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def load_project_env(force: bool = False) -> dict[str, str]:
    """`.env`'i öncelik kuralına göre uygular; kaynak haritasını döndürür.

    Dönen sözlük: anahtar → "process_env" | "project_env".
    Değer ASLA döndürülmez."""
    global _loaded
    if _loaded and not force:
        return dict(_sources)
    _loaded = True
    _sources.clear()
    if is_replit():
        for key in OVERRIDE_KEYS:
            if os.environ.get(key):
                _sources[key] = "process_env"
        return dict(_sources)
    file_vals = _parse_env_file(ENV_FILE)
    for key, val in file_vals.items():
        if key in OVERRIDE_KEYS:
            # Yerel geliştirmede proje .env açık kaynak: stale user/system
            # env değerinin sessizce öne geçmesini engelle.
            os.environ[key] = val
            _sources[key] = "project_env"
        else:
            if key in os.environ:
                _sources[key] = "process_env"
            else:
                os.environ[key] = val
                _sources[key] = "project_env"
    for key in OVERRIDE_KEYS:
        if key not in _sources and os.environ.get(key):
            _sources[key] = "process_env"
    return dict(_sources)


def credential_metadata() -> dict[str, dict]:
    """Yalnız güvenli metadata; hiçbir değer içermez."""
    meta: dict[str, dict] = {}
    for key in OVERRIDE_KEYS:
        val = os.environ.get(key, "")
        meta[key] = {
            "present": bool(val),
            "source": _sources.get(key,
                                   "process_env" if val else "absent"),
            "length": len(val),
            "ascii": val.isascii() if val else None,
        }
    return meta


def write_env_vars(mapping: dict[str, str]) -> None:
    """Verilen anahtar/değer çiftlerini proje .env dosyasına yaz ve
    os.environ'u hemen güncelle.

    - Anahtar zaten .env içindeyse satır yerine yazılır (update).
    - Anahtar yoksa dosyanın sonuna eklenir.
    - Değer ASLA loglanmaz.
    - Sadece yerel/Windows ortamında kullanılmalıdır; Replit'te ValueError üretir.

    Dikkat: Bu fonksiyon yalnızca ilk kurulum sihirbazı tarafından çağrılır.
    """
    if is_replit():
        raise ValueError("Replit ortamında .env dosyasına yazılamaz; Secrets kullanın.")
    # Mevcut içeriği oku
    try:
        text = ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        text = ""
    lines = text.splitlines(keepends=True)
    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in mapping:
            # Mevcut satırı yeni değerle değiştir
            new_lines.append(f"{key}={mapping[key]}\n")
            updated_keys.add(key)
        else:
            new_lines.append(line)
    # Yeni anahtarları ekle
    for key, val in mapping.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")
    ENV_FILE.write_text("".join(new_lines), encoding="utf-8")
    # os.environ'u hemen güncelle (yeniden başlatma gerektirmez)
    for key, val in mapping.items():
        os.environ[key] = val
        _sources[key] = "project_env"


def reset_for_tests() -> None:
    """Yalnız testler için: idempotency bayrağını sıfırlar."""
    global _loaded
    _loaded = False
    _sources.clear()
