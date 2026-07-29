"""
app.py — Alpha-20 v1 PAPER Bot Kontrol Paneli
Flask web arayüzü: bot yönetimi, ayarlar, coin listesi,
Akıllı Coin Seçimi ve Uyarlanabilir Karar & Risk Motoru.
API anahtarı, canlı emir veya gerçek para işlemi içermez.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import secrets
import uuid

from flask import Flask, Response, g, jsonify, render_template, request, redirect, session, url_for
from flask_wtf.csrf import CSRFProtect, CSRFError

# ── Tek environment yükleyici (idempotent) ───────────────────────────────────
# Replit/production: process env (Secrets) kazanır; .env okunmaz.
# Windows yerel: proje .env, Binance TR credential'ları için açık kaynaktır.
# serve_windows.py de çağırır; local_env çifte yüklemeyi kendisi engeller.
# (Eski gömülü setdefault yükleyicisi kaldırıldı: stale OS env
# değerleri .env'in önüne geçebiliyordu — bkz. local_env.py.)
import local_admin
import local_env
local_env.load_project_env()

ROOT_DIR = Path(__file__).resolve().parent

# ── alpha20_v1/ modülleri sys.path üzerinden import ──────────────────────────
sys.path.insert(0, str(ROOT_DIR / "alpha20_v1"))

import universe_manager as um    # noqa: E402
import metrics_store    as ms    # noqa: E402
import safety_guard     as sg    # noqa: E402
import auto_controller  as ac    # noqa: E402
import learning_engine  as le    # noqa: E402
import auth                      # noqa: E402
import security_log     as slog  # noqa: E402

app = Flask(__name__)

# ── Proxy güveni ──────────────────────────────────────────────────────────────
# X-Forwarded-For başlığına varsayılan olarak GÜVENİLMEZ. auth.get_client_ip()
# başlığı yalnızca isteğin doğrudan geldiği soket adresi (remote_addr)
# TRUSTED_PROXY_IPS ortam değişkeninde listelenen güvenilir proxy'lerden
# biriyse ve o zaman da yalnızca proxy'nin eklediği SON girdiyi dikkate alır.
# Bkz. auth.get_client_ip(). Bu, sahte başlıkla rate limit atlatmayı önler:
# başlık ya tamamen yok sayılır ya da saldırganın kontrol edemediği,
# güvenilir proxy'nin yazdığı girdi kullanılır.

# ── Güvenlik yapılandırması ───────────────────────────────────────────────────
_secret = (
    os.environ.get("FLASK_SECRET_KEY") or
    os.environ.get("SESSION_SECRET") or None
)
if not _secret:
    _secret = secrets.token_hex(32)
    print(
        "[WARN] FLASK_SECRET_KEY/SESSION_SECRET tanımlı değil. "
        "Geçici key üretildi; yeniden başlatmada oturumlar geçersiz olur.",
        flush=True,
    )

app.config.update({
    "SECRET_KEY":                 _secret,
    "SESSION_COOKIE_HTTPONLY":    True,
    "SESSION_COOKIE_SAMESITE":    "Lax",
    "SESSION_COOKIE_SECURE":      os.environ.get("FLASK_ENV") == "production",
    "PERMANENT_SESSION_LIFETIME": 8 * 3600,
    "WTF_CSRF_TIME_LIMIT":        3600,
})

csrf = CSRFProtect(app)

ROOT        = ROOT_DIR
CONFIG_PATH = ROOT / "alpha20_v1" / "config.json"
STATE_PATH  = ROOT / "alpha20_v1" / "state.json"
LOG_PATH    = ROOT / "alpha20_v1" / "alpha20.log"
BOT_PATH    = ROOT / "alpha20_v1" / "alpha20.py"
PID_PATH    = ROOT / "alpha20_v1" / ".bot.pid"
BOT_OUTPUT  = ROOT / "alpha20_v1" / "bot_process.log"


def _ensure_paper_state() -> None:
    """İLK KURULUM: PAPER defteri (state.json) yoksa güvenli başlangıç
    defterini oluşturur. Temiz clone'da bu dosya gitignore'ludur ve
    yokluğu Kağıt Hesap kartını yanlış CONNECTION_FAILED gösteriyordu.

    Kurallar:
    - Şekil alpha20_v1/alpha20.py:initial_state ile birebir aynıdır.
    - Bakiye config.json'daki starting_balance_usdt'den okunur; config
      okunamazsa dosya YARATILMAZ (tahmin yasak) ve uyarı loglanır.
    - Yalnız mode=PAPER config'inde çalışır (fail-closed).
    - "x" (exclusive create) ile yazılır: çok worker'lı gunicorn'da
      yarış olsa bile defter asla üzerine yazılmaz; veri taşıma yok.
    """
    if STATE_PATH.exists():
        return
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict) or cfg.get("mode") != "PAPER":
            logging.getLogger(__name__).warning(
                "state.json yok ama config mode=PAPER değil — "
                "başlangıç defteri oluşturulmadı (fail-closed).")
            return
        start = float(cfg["starting_balance_usdt"])
        state = {
            "balance": start,
            "day": datetime.now(timezone.utc).date().isoformat(),
            "day_start_balance": start,
            "consecutive_losses": 0,
            "position": None,
            "trades": [],
        }
        with open(STATE_PATH, "x", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        logging.getLogger(__name__).info(
            "İLK KURULUM: PAPER başlangıç defteri oluşturuldu "
            "(bakiye=%s USDT).", start)
    except FileExistsError:
        return  # başka worker önce yazdı — dokunma
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logging.getLogger(__name__).warning(
            "İLK KURULUM: PAPER defteri oluşturulamadı (%s: %s) — "
            "Kağıt Hesap kartı UNKNOWN kalabilir.",
            type(exc).__name__, exc)


_ensure_paper_state()

CONFIG_LOCK           = threading.RLock()
INTEGER_PATTERN       = re.compile(r"^[0-9]+$")
SYMBOL_PATTERN        = re.compile(r"^[A-Z0-9]+USDT$")
LOG_TIMESTAMP_PATTERN = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}")

DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "PAPER",
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "minimum_score": 65, "scan_seconds": 60,
    "risk_per_trade_pct": 0.5, "daily_loss_limit_pct": 1.5,
    "max_consecutive_losses": 3, "reward_risk_ratio": 2.0,
    "atr_stop_multiplier": 1.5, "max_open_positions": 1,
}

SETTING_RULES: dict[str, tuple[str, float, float]] = {
    "minimum_score":           ("int",   0,   100),
    "scan_seconds":            ("int",   15,  3600),
    "risk_per_trade_pct":      ("float", 0.1, 2.0),
    "daily_loss_limit_pct":    ("float", 0.5, 10.0),
    "max_consecutive_losses":  ("int",   1,   10),
    "reward_risk_ratio":       ("float", 1.0, 5.0),
    "atr_stop_multiplier":     ("float", 0.5, 5.0),
    "max_open_positions":      ("int",   1,   5),
}

ADAPTIVE_SETTING_RULES: dict[str, tuple[str, float, float]] = {
    "regime_min_confidence":      ("float", 0,   100),
    "final_decision_threshold":   ("float", 50,  100),
    "base_risk_pct":              ("float", 0.05, 0.50),
    "max_risk_pct":               ("float", 0.05, 0.50),
    "daily_loss_limit_pct":       ("float", 0.1,  5.0),
    "max_drawdown_pct":           ("float", 1.0,  20.0),
    "max_consecutive_losses":     ("int",   1,    10),
    "risk_reduction_after_losses":("int",   1,    10),
    "learning_interval_hours":    ("float", 1,    168),
    "minimum_learning_trades":    ("int",   5,    200),
    "max_daily_weight_change_pct":("float", 0.5,  20.0),
    "cooldown_minutes":           ("int",   0,    1440),
}

DEFAULT_PRESETS = {
    "default": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "top10": ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
              "DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","SUIUSDT"],
    "top20": ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
              "DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","SUIUSDT",
              "LTCUSDT","BCHUSDT","DOTUSDT","TRXUSDT","UNIUSDT",
              "ETCUSDT","ATOMUSDT","NEARUSDT","ICPUSDT","AAVEUSDT"],
}


@app.template_filter("fmt_volume")
def fmt_volume_filter(vol: float) -> str:
    return um.fmt_volume(vol)


# ══════════════════════════════════════════════════════════════════════════════
# Güvenlik ara katmanı
# ══════════════════════════════════════════════════════════════════════════════

@app.before_request
def _assign_request_id():
    """Her isteğe benzersiz istek kimliği ata (Mission 1400.1)."""
    g.request_id = uuid.uuid4().hex[:16]


def _api_error(message: str, status: int):
    """Güvenli, yapılandırılmış API hata yanıtı (istek kimliği dahil)."""
    return jsonify({"error": message,
                    "request_id": getattr(g, "request_id", "")}), status


def _replit_dev_bypass_active() -> bool:
    """GEÇİCİ geliştirme bypass'ı — SADECE Replit çalışma alanında.

    Üç kilit birden gerekir (default KAPALI):
    1. REPLIT_DEV_BYPASS=1 açıkça set edilmiş olmalı.
    2. Replit workspace belirteci (REPL_ID / REPLIT_DEV_DOMAIN) olmalı —
       Windows/local'de flag set edilse bile bypass ÇALIŞMAZ.
    3. REPLIT_DEPLOYMENT (yayınlanmış üretim) varsa bypass ASLA çalışmaz.

    NOT: Bu blok bilinçli olarak buradadır — görev ajanları tarafından
    "kapsam dışı" diye SİLİNMEMELİDİR (bir kez silindi ve login ekranı
    geri geldi). Kaldırma kararını yalnız operatör verir.
    """
    if os.environ.get("REPLIT_DEV_BYPASS") != "1":
        return False
    if os.environ.get("REPLIT_DEPLOYMENT"):
        return False
    return bool(os.environ.get("REPL_ID")
                or os.environ.get("REPLIT_DEV_DOMAIN"))


def _local_dev_bypass_active() -> bool:
    """GEÇİCİ geliştirme bypass'ı — SADECE Windows/lokal geliştirme.

    Üç kilit birden gerekir (default KAPALI):
    1. LOCAL_DEV_BYPASS=1 açıkça set edilmiş olmalı (.env'e yazılabilir).
    2. Replit ortamı OLMAMALI (REPL_ID/REPLIT_DEV_DOMAIN yok) — Replit
       tarafı kendi REPLIT_DEV_BYPASS flag'ini kullanır.
    3. Üretim OLMAMALI: FLASK_ENV=production veya REPLIT_DEPLOYMENT
       varsa bypass ASLA çalışmaz.

    NOT: Görev ajanları tarafından "kapsam dışı" diye SİLİNMEMELİDİR —
    kaldırma kararını yalnız operatör verir.
    """
    if os.environ.get("LOCAL_DEV_BYPASS") != "1":
        return False
    if os.environ.get("REPLIT_DEPLOYMENT"):
        return False
    if os.environ.get("FLASK_ENV") == "production":
        return False
    return not (os.environ.get("REPL_ID")
                or os.environ.get("REPLIT_DEV_DOMAIN"))


@app.before_request
def _security_gate():
    """Her istekte kimlik doğrulama kontrolü. TESTING=True ise atlanır."""
    if app.config.get("TESTING"):
        app.config["WTF_CSRF_ENABLED"] = False
        return
    if _replit_dev_bypass_active():
        if not session.get("logged_in"):
            auth.start_session("replit-dev-bypass")
            logging.getLogger(__name__).warning(
                "AUTH BYPASS AKTİF (REPLIT_DEV_BYPASS=1) — test kullanıcısı "
                "'replit-dev-bypass' otomatik giriş yaptı. Bu yalnız Replit "
                "geliştirme ortamı içindir; üretimde çalışmaz.")
        return
    if _local_dev_bypass_active():
        if not session.get("logged_in"):
            auth.start_session("local-dev-bypass")
            logging.getLogger(__name__).warning(
                "AUTH BYPASS AKTİF (LOCAL_DEV_BYPASS=1) — test kullanıcısı "
                "'local-dev-bypass' otomatik giriş yaptı. Bu yalnız "
                "Windows/lokal geliştirme içindir; Replit ve üretimde "
                "çalışmaz.")
        return
    exempt = {"/login", "/logout", "/setup", "/setup/hash", "/setup/save",
              "/setup/check",
              "/favicon.ico", "/health", "/api/v1/health",
              "/api/v1/auth/login"}
    if request.path in exempt or request.path.startswith("/static/"):
        return
    is_api = request.path.startswith("/api/")
    # Parola yapılandırılmamışsa: KİLİTLİ KURULUM modu
    if not auth.password_hash_configured():
        if is_api:
            slog.log_event(slog.APP_LOCKED, ip=auth.get_client_ip(),
                           detail=f"rid={g.request_id}")
            return _api_error("Kurulum kilitli — yapılandırma eksik.", 403)
        return redirect(url_for("setup_wizard"))
    if not session.get("logged_in"):
        if is_api:
            slog.log_event(slog.UNAUTHORIZED_API, ip=auth.get_client_ip(),
                           detail=f"rid={g.request_id} path={request.path[:60]}")
            return _api_error("Yetkisiz erişim. Giriş yapın.", 401)
        # Mission 2400 route fix: önceki rota geri yüklenmez —
        # giriş sonrası her zaman Trading Home açılır (next yok).
        return redirect(url_for("login"))
    if auth._session_expired():
        session.clear()
        slog.log_event(slog.SESSION_EXPIRED, ip=auth.get_client_ip(),
                       detail=f"rid={g.request_id}")
        if is_api:
            return _api_error("Oturum süresi doldu. Tekrar deneyin.", 401)
        return redirect(url_for("login"))


@app.after_request
def _security_headers(response: Response) -> Response:
    """Tüm yanıtlara güvenlik HTTP başlıkları ekle."""
    response.headers["X-Request-ID"] = getattr(g, "request_id", "")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]     = "geolocation=(), camera=(), microphone=()"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp
    if os.environ.get("FLASK_ENV") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.errorhandler(404)
def _not_found(_err):
    """Bilinmeyen/geçersiz rota — Mission 2400 route fix.

    HTML istekleri Trading Home'a yönlendirilir; API istekleri
    sterile 404 zarfı alır (istemci mantığı bozulmaz).
    """
    if request.path.startswith("/api/"):
        return _api_error("Kaynak bulunamadı.", 404)
    return redirect("/home")


@app.errorhandler(CSRFError)
def _csrf_error(exc: CSRFError):  # type: ignore[misc]
    ip = auth.get_client_ip()
    slog.log_event(slog.CSRF_FAIL, ip=ip, detail=str(exc)[:80])
    if request.path.startswith("/api/"):
        return _api_error("Güvenlik hatası: CSRF doğrulaması başarısız.", 400)
    if request.path.startswith("/setup"):
        # Sihirbaz fetch ile JSON bekler — asla HTML/redirect döndürme.
        return {"ok": False, "error": {
            "code": "CSRF_FAILED",
            "message": "Oturum doğrulaması başarısız. Sayfayı yenileyip "
                       "tekrar deneyin."}}, 403
    # Kimlik doğrulanmamış istekte dashboard içeriği ASLA gönderme.
    # Oturum yoksa veya süresi dolmuşsa login'e yönlendir (302).
    if not session.get("logged_in") or auth._session_expired():
        session.clear()
        return redirect(url_for("login")), 302
    return render_dashboard(
        "Güvenlik hatası: CSRF token geçersiz veya süresi dolmuş. Lütfen sayfayı yenileyin.",
        "error"
    ), 400


# ══════════════════════════════════════════════════════════════════════════════
# Başlangıç doğrulama
# ══════════════════════════════════════════════════════════════════════════════

def validate_startup_config() -> None:
    """Kritik yapılandırmayı kontrol et ve güvenlik loguna yaz."""
    secret_ok = bool(os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SESSION_SECRET"))
    hash_ok   = auth.password_hash_configured()
    warnings: list[str] = []
    if not secret_ok:
        warnings.append("FLASK_SECRET_KEY/SESSION_SECRET tanımlı değil (geçici key kullanılıyor)")
    if not hash_ok:
        warnings.append("ADMIN_PASSWORD_HASH tanımlı değil (giriş devre dışı)")
        print(
            "[WARN] ADMIN_PASSWORD_HASH ortam değişkeni ayarlanmamış. "
            "Dashboard erişimi devre dışı. Kurulum için SECURITY.md belgesi.\n"
            "  Hash üretmek: python3 -c \""
            "from werkzeug.security import generate_password_hash; "
            "import getpass; print(generate_password_hash(getpass.getpass()))\"",
            flush=True,
        )
    detail = "; ".join(warnings) if warnings else "Yapılandırma tamam."
    slog.log_event(slog.STARTUP, detail=detail)


def enforce_paper_mode_lock() -> None:
    """Başlangıçta config.json'un PAPER modunda olduğunu garanti et."""
    with CONFIG_LOCK:
        cfg, err = load_config()
        if err or cfg is None:
            slog.log_event(slog.CONFIG_ERROR, detail=f"config.json okunamadı: {err}")
            return
        if cfg.get("mode") != "PAPER":
            cfg["mode"] = "PAPER"
            try:
                atomic_write_json(CONFIG_PATH, cfg)
                slog.log_event(slog.PAPER_MODE_ACTIVE, detail="Mode zorla PAPER'a alındı.")
            except OSError as exc:
                slog.log_event(slog.CONFIG_ERROR, detail=f"Mode düzeltilemedi: {exc}")
        else:
            slog.log_event(slog.PAPER_MODE_ACTIVE, detail="PAPER modu doğrulandı.")


# ══════════════════════════════════════════════════════════════════════════════
# Yürütme modu (Mission 2100 Controlled Execution katmanı)
# ══════════════════════════════════════════════════════════════════════════════

# Kapalı küme: LIVE bilinçli olarak YOKTUR (fail-closed).
# PAPER      → simüle yürütme (defter üzerinden)
# SHADOW     → gerçek piyasa gözlemi + simüle karşılaştırma (emir yazmaz)
# MICRO_LIVE → yalnızca yetkilendirme talebi üretir (borsaya emir YAZMAZ)
EXECUTION_MODES = ("PAPER", "SHADOW", "MICRO_LIVE")
EXECUTION_MODE_LABELS = {
    "PAPER": "PAPER",
    "SHADOW": "SHADOW",
    "MICRO_LIVE": "MICRO LIVE",
}


def get_execution_mode(config: dict[str, Any] | None) -> str:
    """Seçili yürütme modunu döndür; geçersiz değer fail-closed PAPER olur."""
    mode = (config or {}).get("execution_mode", "PAPER")
    return mode if mode in EXECUTION_MODES else "PAPER"


# ══════════════════════════════════════════════════════════════════════════════
# Config helpers
# ══════════════════════════════════════════════════════════════════════════════

def read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"{path.name} dosyası bulunamadı."
    try:
        with path.open("r", encoding="utf-8") as f:
            val = json.load(f)
        if not isinstance(val, dict):
            return None, f"{path.name} geçerli bir nesne içermiyor."
        return val, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name} okunamadı ({type(exc).__name__})."


def load_config() -> tuple[dict[str, Any] | None, str | None]:
    cfg, err = read_json(CONFIG_PATH)
    if err or cfg is None:
        return None, err
    if not isinstance(cfg.get("symbols"), list):
        return None, "config.json içindeki symbols listesi geçersiz."
    return cfg, None


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def update_config(updates: dict[str, Any]) -> tuple[bool, str]:
    with CONFIG_LOCK:
        cfg, err = load_config()
        if err or cfg is None:
            return False, err or "Ayar dosyası okunamadı."
        updated = dict(cfg)
        updated.update(updates)
        try:
            atomic_write_json(CONFIG_PATH, updated)
        except OSError as exc:
            return False, f"Ayarlar kaydedilemedi ({type(exc).__name__})."
    return True, "Ayarlar başarıyla kaydedildi."


def parse_setting(name: str, raw: str | None, rules: dict | None = None) -> int | float | None:
    if raw is None:
        return None
    r = rules or SETTING_RULES
    if name not in r:
        return None
    kind, lo, hi = r[name]
    val = raw.strip()
    if kind == "int":
        if not INTEGER_PATTERN.fullmatch(val):
            return None
        parsed: int | float = int(val)
    else:
        try:
            d = Decimal(val)
            if not d.is_finite():
                return None
            parsed = float(d)
        except (InvalidOperation, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
    return parsed if lo <= parsed <= hi else None


def normalize_symbol(raw: str | None) -> str | None:
    if raw is None:
        return None
    sym = raw.strip().upper()
    return sym if SYMBOL_PATTERN.fullmatch(sym) else None


def save_symbols(symbols: list[str]) -> tuple[bool, str]:
    return update_config({"symbols": symbols})


# ══════════════════════════════════════════════════════════════════════════════
# Bot süreç yönetimi
# ══════════════════════════════════════════════════════════════════════════════

def _proc_fs_available() -> bool:
    """Linux /proc dosya sistemi kullanılabilir mi? (Windows'ta yok.)"""
    return os.name != "nt" and Path("/proc").exists()


def _pid_alive(pid: int) -> bool:
    """Platform-bağımsız süreç canlılık kontrolü.

    Linux/POSIX: os.kill(pid, 0) — sinyal göndermez, yalnız yoklar.
    Windows: os.kill(pid, 0) süreci ÖLDÜRÜR (TerminateProcess), bu yüzden
    ctypes ile OpenProcess denenir; açılabiliyorsa süreç yaşıyordur.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, 0, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            # Windows API'ye erişilemedi (ör. test simülasyonu) —
            # fail-safe: süreç yok say.
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _pidfile_bot_pid() -> int | None:
    """PID dosyası tabanlı platform-bağımsız bot tespiti.

    Yalnız /proc'un OLMADIĞI ortamlarda (Windows) anlamlıdır: PID dosyası
    okunur ve süreç hâlâ canlıysa PID döner. /proc'lu Linux'ta cmdline
    doğrulaması yapılabildiği için bu yol kullanılmaz (davranış değişmez).
    """
    pid = read_pid()
    if pid is None:
        return None
    return pid if _pid_alive(pid) else None


def process_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]
    except (OSError, ValueError):
        return None


def is_bot_command(pid: int) -> bool:
    cmd = process_cmdline(pid)
    if not cmd:
        return False
    return any(Path(p).resolve() == BOT_PATH for p in cmd if p.endswith(".py"))


def find_bot_pids() -> list[int]:
    # Windows uyumluluğu: /proc yalnız Linux'ta vardır. Windows'ta
    # (veya /proc yoksa) tarama yapılmaz ve güvenle "bot yok" dönülür —
    # aksi halde Path("/proc").iterdir() FileNotFoundError fırlatır ve
    # /api/v1/executive/summary dahil çağıran uçlar HTTP 500'e düşer.
    proc = Path("/proc")
    if os.name == "nt" or not proc.exists():
        return []
    pids: list[int] = []
    try:
        for entry in proc.iterdir():
            if entry.name.isdigit():
                pid = int(entry.name)
                if pid != os.getpid() and is_bot_command(pid):
                    pids.append(pid)
    except OSError as exc:
        # Savunma katmanı: beklenmeyen OS hatası PID taraması yüzünden
        # endpoint'i asla 500'e düşürmesin.
        logging.getLogger("alpha.app").warning(
            "PID taraması başarısız (%s) — bot bulunamadı sayılıyor.", exc)
        return []
    return pids


def read_pid() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        payload = json.loads(PID_PATH.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
        return pid if pid > 0 else None
    except Exception:
        return None


def write_pid(pid: int) -> None:
    atomic_write_json(PID_PATH, {"pid": pid,
                                  "started_at": datetime.now(timezone.utc).isoformat()})


def bot_running() -> bool:
    # Linux: /proc taraması (davranış değişmedi). Windows / /proc'suz
    # ortam: PID dosyası + canlılık kontrolü ile gerçek durum yansıtılır.
    if _proc_fs_available():
        return bool(find_bot_pids())
    return _pidfile_bot_pid() is not None


def start_bot() -> tuple[bool, str]:
    with CONFIG_LOCK:
        if bot_running():
            return False, "Bot zaten çalışıyor."
        if not BOT_PATH.exists():
            return False, "Bot dosyası bulunamadı."
        try:
            out = BOT_OUTPUT.open("a", encoding="utf-8")
            popen_kwargs: dict[str, Any] = {
                "cwd": str(ROOT), "stdin": subprocess.DEVNULL,
                "stdout": out, "stderr": subprocess.STDOUT,
                "close_fds": True,
            }
            if os.name == "nt":
                # Windows: start_new_session POSIX'e özgüdür; panel süreci
                # kapansa da bot yaşasın diye ayrı süreç grubu + detach.
                popen_kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0))
            else:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                [sys.executable, str(BOT_PATH)], **popen_kwargs)
            write_pid(proc.pid)
            out.close()
        except (OSError, ValueError) as exc:
            return False, f"Bot başlatılamadı ({type(exc).__name__})."
    return True, "Bot başlatıldı."


def stop_bot() -> tuple[bool, str]:
    with CONFIG_LOCK:
        pid = read_pid()
        # Linux: cmdline /proc'tan doğrulanır (yanlış süreci öldürme).
        # Windows: /proc yok — PID dosyası + canlılık kontrolü yeterlidir
        # (dosyayı yalnız bu uygulama yazar).
        if _proc_fs_available():
            verified = pid is not None and is_bot_command(pid)
        else:
            verified = pid is not None and _pid_alive(pid)
        if pid is None or not verified:
            PID_PATH.unlink(missing_ok=True)
            return False, "Uygulamanın başlattığı çalışan bot bulunamadı."
        try:
            # Windows'ta os.kill(pid, SIGTERM) TerminateProcess'e eşdeğerdir
            # (koşulsuz sonlandırma); POSIX'te nazik SIGTERM gönderilir.
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _pid_alive(pid) and hasattr(signal, "SIGKILL"):
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            return False, f"Bot durdurulamadı ({type(exc).__name__})."
        PID_PATH.unlink(missing_ok=True)
    return True, "Bot durduruldu."


# ══════════════════════════════════════════════════════════════════════════════
# Durum okuma
# ══════════════════════════════════════════════════════════════════════════════

def _display(val: Any, fallback: str = "Bilinmiyor") -> str:
    if val is None:
        return fallback
    if isinstance(val, float):
        return f"{val:.8g}"
    return str(val)


def read_last_log() -> tuple[str, str]:
    if not LOG_PATH.exists():
        return "Log dosyası bulunamadı.", "Bilinmiyor"
    try:
        lines = [ln.strip() for ln in
                 LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
                 if ln.strip()]
        last  = lines[-1] if lines else "Henüz log yok."
        m     = LOG_TIMESTAMP_PATTERN.match(last)
        scan  = f"{m.group('ts')} UTC" if m else datetime.fromtimestamp(
            LOG_PATH.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        return last[-500:], scan
    except OSError as exc:
        return f"Log okunamadı ({type(exc).__name__}).", "Bilinmiyor"


def build_status() -> tuple[dict[str, Any], str | None]:
    state, state_err = read_json(STATE_PATH)
    log_msg, last_scan = read_last_log()
    state  = state or {}
    trades = state.get("trades") if isinstance(state.get("trades"), list) else []
    return {
        "running":            bot_running(),
        "balance":            _display(state.get("balance")),
        "day_start_balance":  _display(state.get("day_start_balance")),
        "consecutive_losses": _display(state.get("consecutive_losses"), "0"),
        "trade_count":        len(trades),
        "day":                _display(state.get("day")),
        "last_scan":          last_scan,
        "last_log":           log_msg,
        "position":           state.get("position") if isinstance(state.get("position"), dict) else None,
        "trades":             list(reversed(trades[-10:])),
    }, state_err


def setting_fields(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    cfg    = config or DEFAULT_CONFIG
    labels = {
        "minimum_score":          ("Minimum Sinyal Skoru",     "number", "1"),
        "scan_seconds":           ("Tarama Aralığı (sn)",       "number", "1"),
        "risk_per_trade_pct":     ("İşlem Başına Risk (%)",      "number", "0.1"),
        "daily_loss_limit_pct":   ("Günlük Zarar Limiti (%)",   "number", "0.1"),
        "max_consecutive_losses": ("Maks. Ardışık Zarar",        "number", "1"),
        "reward_risk_ratio":      ("Ödül / Risk Oranı",          "number", "0.1"),
        "atr_stop_multiplier":    ("ATR Stop Çarpanı",           "number", "0.1"),
        "max_open_positions":     ("Maks. Açık Pozisyon",        "number", "1"),
    }
    fields = []
    for name, (kind, lo, hi) in SETTING_RULES.items():
        label, inp_type, step = labels[name]
        fields.append({
            "name": name, "label": label, "input_type": inp_type,
            "min": lo, "max": hi, "step": step,
            "value": cfg.get(name, DEFAULT_CONFIG.get(name, "")),
        })
    return fields


# ══════════════════════════════════════════════════════════════════════════════
# Akıllı seçim bağlamı
# ══════════════════════════════════════════════════════════════════════════════

def build_smart_context(config: dict[str, Any] | None) -> tuple[dict, dict]:
    smart_cfg   = um.get_smart_config()
    suggestions = smart_cfg.get("last_suggestions", [])
    change_log  = um.get_smart_log()[:20]
    last_ts     = smart_cfg.get("last_analysis_time")
    last_str    = (datetime.fromisoformat(last_ts).strftime("%Y-%m-%d %H:%M UTC")
                   if last_ts else None)
    state, _    = read_json(STATE_PATH)
    trades      = (state or {}).get("trades", [])
    if not isinstance(trades, list):
        trades = []
    manual_list = smart_cfg.get("manual_list")
    perf        = um.get_performance_comparison(trades, manual_list)
    smart       = {
        "mode":           smart_cfg.get("mode", "MANUEL"),
        "cfg":            smart_cfg,
        "suggestions":    suggestions,
        "change_log":     change_log,
        "last_analysis":  last_str,
        "next_analysis":  um.next_analysis_str(smart_cfg),
        "candidate_count": smart_cfg.get("candidate_count", 0),
        "running":        smart_cfg.get("analysis_running", False) or um.analysis_status["running"],
        "manual_list":    manual_list,
        "pinned":         set(smart_cfg.get("pinned", [])),
        "error":          um.analysis_status.get("error"),
    }
    return smart, perf


# ══════════════════════════════════════════════════════════════════════════════
# Uyarlanabilir motor bağlamı
# ══════════════════════════════════════════════════════════════════════════════

def build_adaptive_context(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg         = config or DEFAULT_CONFIG
    adaptive    = cfg.get("adaptive_system", {})
    panel_st    = ms.read_panel_status()
    ctrl_st     = ac.get_status()
    safety_st   = sg.get_safety_state()
    learn_panel = {}
    try:
        learn_panel = le.get_panel_data()
    except Exception:
        pass
    recent_dec   = ms.get_recent_decisions(20)
    recent_risk  = ms.get_recent_risk_events(10)
    recent_err   = ms.get_recent_errors(5)

    # Günlük rapor özeti
    daily_report = _build_daily_report(config)

    return {
        "adaptive":      adaptive,
        "ctrl":          ctrl_st,
        "safety":        safety_st,
        "panel":         panel_st,
        "learn":         learn_panel,
        "decisions":     recent_dec,
        "risk_events":   recent_risk,
        "errors":        recent_err,
        "daily_report":  daily_report,
        "market_regime": panel_st.get("market_regime", {}),
    }


def _build_daily_report(config: dict | None) -> dict[str, Any]:
    state, _ = read_json(STATE_PATH)
    if not state:
        return {}
    trades    = state.get("trades", [])
    if not isinstance(trades, list):
        trades = []
    today     = datetime.now(timezone.utc).date().isoformat()
    today_t   = [t for t in trades if (t.get("closed_at", "") or "").startswith(today)]
    balance   = float(state.get("balance", 0))
    day_start = float(state.get("day_start_balance", balance) or balance)
    daily_pnl = round(balance - day_start, 4)
    n         = len(today_t)
    if n == 0:
        return {
            "trade_count": 0, "daily_pnl": daily_pnl,
            "start_balance": day_start, "end_balance": balance,
            "win_rate": None, "profit_factor": None,
        }
    wins    = [t for t in today_t if t.get("result") == "WIN"]
    wr      = round(len(wins) / n * 100, 1)
    pos_pnl = sum(float(t.get("pnl", 0) or 0) for t in today_t if float(t.get("pnl", 0) or 0) > 0)
    neg_pnl = abs(sum(float(t.get("pnl", 0) or 0) for t in today_t if float(t.get("pnl", 0) or 0) < 0))
    pf      = round(pos_pnl / neg_pnl, 3) if neg_pnl > 0 else None
    by_sym  = {}
    for t in today_t:
        s = t.get("symbol", "?")
        by_sym.setdefault(s, []).append(float(t.get("pnl", 0) or 0))
    sym_pnl = {s: round(sum(v), 4) for s, v in by_sym.items()}
    best_c  = max(sym_pnl, key=lambda k: sym_pnl[k], default="—")
    worst_c = min(sym_pnl, key=lambda k: sym_pnl[k], default="—")
    return {
        "trade_count":    n,
        "daily_pnl":      daily_pnl,
        "start_balance":  day_start,
        "end_balance":    balance,
        "win_rate":       wr,
        "profit_factor":  pf,
        "best_coin":      best_c,
        "worst_coin":     worst_c,
        "date":           today,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Kimlik doğrulama rotaları
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Sağlık kontrolü (kimlik doğrulamasız — izleme araçları için)
# ══════════════════════════════════════════════════════════════════════════════

_APP_START_TIME = time.time()


@app.get("/health")
def health():
    """Basit sağlık kontrol uç noktası.

    Yanıt: HTTP 200 + JSON  {"status": "ok", "uptime_s": <float>, "pid": <int>}
    Kimlik doğrulama gerektirmez; izleme araçları ve yük dengeleyiciler için.
    """
    return {
        "status": "ok",
        "uptime_s": round(time.time() - _APP_START_TIME, 1),
        "pid": os.getpid(),
    }, 200


# ══════════════════════════════════════════════════════════════════════════════
# İlk kurulum sihirbazı
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/setup")
def setup_wizard():
    """İlk çalıştırma sihirbazı — yalnızca parola yapılandırılmamışsa erişilebilir."""
    if auth.password_hash_configured():
        # Kurulum tamamlanmışsa sihirbazı tamamen gizle (404).
        # 302 yerine 404 döndürmek, endpoint'in varlığını ifşa etmez.
        return "", 404
    return render_template("setup.html", is_replit=local_env.is_replit())


@app.post("/setup/hash")
def setup_generate_hash():
    """
    Girilen paroladan Werkzeug PBKDF2 hash üret ve döndür.
    Parola sunucuda saklanmaz; yalnızca hash döndürülür.
    Yalnızca ADMIN_PASSWORD_HASH yapılandırılmamışken erişilebilir.
    """
    if auth.password_hash_configured():
        # Kurulum sonrası /setup ile aynı davranış: varlık ifşa etmeyen 404.
        return "", 404
    # Hız sınırı (Mission 1400.1-R): ayrı "setup:" ad alanı kullanılır —
    # sihirbaz istekleri login kilit bütçesini TÜKETMEZ, ama kendi başına
    # aynı pencere/limitle kısıtlanır.
    _key = "setup:" + auth.get_client_ip()
    _allowed, _secs = auth.check_rate_limit(_key)
    def _err(code: str, message: str, status: int):
        return {"ok": False, "error": {"code": code, "message": message}}, status

    if not _allowed:
        return _err("RATE_LIMITED", "Çok fazla deneme yapıldı. Lütfen "
                    f"{_secs} saniye sonra tekrar deneyin.", 429)
    auth.record_attempt(_key, success=False)
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    if not password or not isinstance(password, str):
        return _err("INVALID_PASSWORD", "Parola boş olamaz.", 400)
    if len(password) < 6:
        return _err("INVALID_PASSWORD",
                    "Parola en az 6 karakter olmalıdır.", 400)
    if len(password) > 1024:
        return _err("INVALID_PASSWORD", "Parola çok uzun.", 400)
    from werkzeug.security import generate_password_hash
    pw_hash = generate_password_hash(password)
    ip = auth.get_client_ip()
    slog.log_event(slog.STARTUP, detail="setup: hash generated", ip=ip)
    # "hash" alanı geriye dönük uyumluluk için korunur.
    return {"ok": True, "password_hash": pw_hash, "hash": pw_hash}


@app.post("/setup/save")
def setup_save():
    """Yerel / Windows kurulumu için: hash + kullanıcı adını .env'e yaz ve
    os.environ'u hemen güncelle — yeniden başlatma gerekmez.

    Yalnızca kurulum sihirbazı aktifken (parola yapılandırılmamışken) ve
    Replit DIŞINDA (yerel/.env ortamı) kullanılabilir. Replit'te 403 döner.
    """
    if auth.password_hash_configured():
        return "", 404
    if local_env.is_replit():
        return {"ok": False, "error": {
            "code": "REPLIT_ENV",
            "message": "Replit ortamında bu endpoint kullanılamaz; Secrets'ı kullanın."
        }}, 403
    data = request.get_json(silent=True) or {}
    pw_hash = data.get("password_hash", "")
    username = data.get("username", "")
    if not pw_hash or not isinstance(pw_hash, str):
        return {"ok": False, "error": {"code": "MISSING_HASH", "message": "password_hash boş."}}, 400
    if not username or not isinstance(username, str):
        return {"ok": False, "error": {"code": "MISSING_USERNAME", "message": "Kullanıcı adı boş olamaz."}}, 400
    if len(username) > 64:
        return {"ok": False, "error": {"code": "INVALID_USERNAME", "message": "Kullanıcı adı çok uzun."}}, 400
    if not re.fullmatch(r"[A-Za-z0-9_-]+", username):
        return {"ok": False, "error": {
            "code": "INVALID_USERNAME",
            "message": "Kullanıcı adı yalnızca harf, rakam, alt çizgi (_) ve tire (-) içerebilir; boşluk ve özel karakter kullanılamaz."
        }}, 400
    # Temel hash biçimi doğrulaması (werkzeug pbkdf2:sha256:... veya eski scrypt:)
    if not (pw_hash.startswith("pbkdf2:") or pw_hash.startswith("scrypt:")):
        return {"ok": False, "error": {
            "code": "INVALID_HASH",
            "message": "Geçersiz hash biçimi — yalnızca bu sihirbazdan üretilen hash kabul edilir."
        }}, 400
    try:
        # Windows/yerel giriş kaynağı: data/local_admin.json (atomic yazma,
        # 0600). Environment veya .env'e YAZILMAZ — Secrets'tan tam ayrım.
        local_admin.save(username, pw_hash)
    except Exception:
        import logging
        logging.getLogger(__name__).error("setup/save write failed",
                                          exc_info=True)
        return {"ok": False, "error": {
            "code": "WRITE_ERROR",
            "message": "Yerel kimlik dosyası yazılamadı. Dizin izinlerini "
                       "kontrol edin (data/)."
        }}, 500
    ip = auth.get_client_ip()
    slog.log_event(slog.STARTUP,
                   detail="setup: credentials saved to local_admin store",
                   ip=ip)
    return {"ok": True}


@app.get("/setup/check")
def setup_check():
    """Parola yapılandırılmış mı diye kontrol et (sihirbaz doğrulama adımı).

    Güvenlik kararı: Kurulum tamamlandıktan sonra bu endpoint /setup ile
    aynı şekilde 404 döndürür. Böylece anonim bir istemci, yapılandırma
    durumunu (parolanın ayarlı olup olmadığını) sorgulayamaz ve endpoint'in
    varlığı ifşa edilmez. Yalnızca kurulum sihirbazı aktifken (parola
    yapılandırılmamışken) JSON durum döndürür.
    """
    if auth.password_hash_configured():
        return "", 404
    return {"configured": False}


@app.route("/login", methods=["GET", "POST"])
def login():
    # Parola yapılandırılmamışsa sihirbaza yönlendir
    if not auth.password_hash_configured():
        return redirect(url_for("setup_wizard"))
    if session.get("logged_in"):
        return redirect("/home")

    error: str | None = None
    not_configured    = not auth.password_hash_configured()
    # Mission 2400 route fix: önceki rota ASLA geri yüklenmez;
    # kimlik doğrulama sonrası her zaman Trading Home açılır.
    next_url = "/home"

    if request.method == "POST":
        ip = auth.get_client_ip()
        allowed, secs = auth.check_rate_limit(ip)
        if not allowed:
            slog.log_event(slog.LOGIN_FAIL, detail=f"Rate limited {secs}s", ip=ip)
            error = f"Çok fazla başarısız deneme. {secs} saniye bekleyin."
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if auth.verify_credentials(username, password):
                auth.record_attempt(ip, success=True)
                # Oturum sabitleme (session fixation) önlemi: girişten önce
                # mevcut oturum tamamen temizlenir, yeni oturum başlatılır.
                session.clear()
                auth.start_session(username)
                slog.log_event(slog.LOGIN_OK, username=username, ip=ip)
                return redirect(next_url)
            else:
                auth.record_attempt(ip, success=False)
                slog.log_event(slog.LOGIN_FAIL,
                               detail=f"user={username[:20]}",
                               ip=ip)
                error = "Kullanıcı adı veya parola hatalı."

    return render_template(
        "login.html",
        error=error,
        not_configured=not_configured,
        next=next_url,
    )


@app.get("/logout")
def logout():
    username = auth.clear_session()
    ip       = auth.get_client_ip()
    slog.log_event(slog.LOGOUT, username=username, ip=ip)
    return redirect(url_for("login"))


# ══════════════════════════════════════════════════════════════════════════════
# Parola değiştir (Windows/yerel — data/local_admin.json)
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/settings/password", methods=["GET", "POST"])
def change_password():
    """Girişli operatör için güvenli parola değiştirme.

    - Windows/yerel: mevcut parola doğrulanır, yeni parola hash'lenir ve
      data/local_admin.json ATOMIC olarak güncellenir (local_admin.save).
      Yeniden başlatma gerekmez; dosya elle silinmez.
    - Replit: yerel kimlik deposu devre dışıdır — sayfa yalnızca Secrets
      yönlendirmesi gösterir, POST 403 döner.
    Kaba kuvvete karşı ayrı "pwchange:" rate-limit ad alanı kullanılır;
    login kilit bütçesi tüketilmez.
    """
    is_local = local_admin.enabled()
    error = None
    success = None

    def _render(status: int = 200):
        from version import get_version
        import alpha_platform as ap
        return render_template(
            "account_password.html",
            is_local=is_local, error=error, success=success,
            app_mode=ap.app_mode(), app_version=get_version(),
            owner=session.get("username", ""),
            active_page="change_password",
        ), status

    if request.method == "POST":
        ip = auth.get_client_ip()
        if not is_local:
            return _render(403)
        _key = "pwchange:" + ip
        allowed, secs = auth.check_rate_limit(_key)
        if not allowed:
            slog.log_event(slog.LOGIN_FAIL, ip=ip,
                           detail=f"pwchange rate limited {secs}s")
            error = f"Çok fazla başarısız deneme. {secs} saniye bekleyin."
        else:
            current = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            creds = local_admin.get_credentials()
            if creds is None:
                error = "Yerel kimlik kaydı bulunamadı. Kurulum sihirbazını kullanın."
            elif not auth.verify_credentials(creds[0], current):
                auth.record_attempt(_key, success=False)
                slog.log_event(slog.LOGIN_FAIL, ip=ip,
                               detail="pwchange: mevcut parola dogrulanamadi")
                error = "Mevcut parola hatalı."
            elif not new_pw or len(new_pw) < 6:
                error = "Yeni parola en az 6 karakter olmalıdır."
            elif len(new_pw) > 1024:
                error = "Yeni parola çok uzun."
            elif new_pw != confirm:
                error = "Yeni parolalar birbiriyle eşleşmiyor."
            elif new_pw == current:
                error = "Yeni parola mevcut paroladan farklı olmalıdır."
            else:
                from werkzeug.security import generate_password_hash
                try:
                    local_admin.save(creds[0], generate_password_hash(new_pw))
                except (ValueError, OSError) as exc:
                    logging.getLogger(__name__).error(
                        "pwchange write failed: %s", type(exc).__name__)
                    error = ("Parola dosyası güncellenemedi (yazma hatası). "
                             "Disk izinlerini kontrol edin; mevcut parola "
                             "geçerli kalmaya devam ediyor.")
                else:
                    auth.record_attempt(_key, success=True)
                    slog.log_event(slog.PASSWORD_CHANGE,
                                   username=session.get("username", ""),
                                   ip=ip, detail="pwchange: parola guncellendi")
                    success = ("Parolanız güncellendi. Yeni parola hemen "
                               "geçerli; yeniden başlatma gerekmez.")

    return _render()


# ══════════════════════════════════════════════════════════════════════════════
# Sayfa render
# ══════════════════════════════════════════════════════════════════════════════

def render_dashboard(message: str | None = None, message_type: str = "success"):
    from version import get_version
    config, config_error = load_config()
    status, state_error  = build_status()
    safe_config          = config or DEFAULT_CONFIG
    smart, perf          = build_smart_context(safe_config)
    adaptive_ctx         = build_adaptive_context(safe_config)
    return render_template(
        "dashboard.html",
        config=safe_config, status=status,
        execution_mode=get_execution_mode(safe_config),
        execution_modes=EXECUTION_MODES,
        execution_mode_labels=EXECUTION_MODE_LABELS,
        exec_panel={
            "trade_amount": safe_config.get("trade_amount_usdt", 10),
            "max_positions": safe_config.get("max_open_positions", 1),
            "leverage": "1x",
        },
        setting_fields=setting_fields(config),
        config_error=config_error, state_error=state_error,
        message=message, message_type=message_type,
        smart=smart, perf=perf,
        adaptive=adaptive_ctx,
        security=slog.get_security_summary(),
        app_version=get_version(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Rotalar — temel panel
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def index():
    """Varsayılan açılış rotası — her zaman Trading Home.

    Mission 2400 route fix: başlangıç, yenileme ve yeniden bağlanma
    senaryolarının tümü doğrudan Trading Home'a iner. Eski Başlangıç
    kabuğu /start altında menüden erişilebilir kalır.
    """
    return redirect("/home")


@app.get("/start")
def start_page():
    """Mission 1400.1 — kimlik doğrulamalı uygulama kabuğu (Başlangıç)."""
    from version import get_version
    import alpha_platform as ap
    return render_template(
        "shell.html",
        app_version=get_version(),
        owner=session.get("username", ""),
        setup_state=ap.setup_state(),
        app_mode=ap.app_mode(),
        flags=ap.feature_flags(),
        server_time=datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"),
    )


@app.get("/panel")
def panel():
    """Klasik bot kontrol paneli (Genel Bakış)."""
    return render_dashboard()


@app.post("/settings")
def save_settings():
    updates: dict[str, Any] = {}
    for name in SETTING_RULES:
        parsed = parse_setting(name, request.form.get(name))
        if parsed is None:
            _, lo, hi = SETTING_RULES[name]
            return render_dashboard(f"Hata: {name} değeri {lo}–{hi} arasında olmalıdır.", "error")
        updates[name] = parsed
    ok, msg = update_config(updates)
    if ok and bot_running():
        msg += " Değişikliklerin etkili olması için botu yeniden başlatın."
    if ok:
        ms.append_system_error(component="settings", error_type="SETTINGS_CHANGE",
                               message=f"Ayarlar güncellendi: {list(updates.keys())}")
        slog.log_event(slog.SETTINGS_CHANGE,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip(),
                       detail=str(list(updates.keys()))[:80])
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/coins/add")
def add_coin():
    sym = normalize_symbol(request.form.get("symbol"))
    cfg, err = load_config()
    if err or cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    syms = [str(s) for s in cfg.get("symbols", [])]
    if sym is None:
        return render_dashboard("Hata: Sembol A-Z ve 0-9 içermeli, USDT ile bitmeli.", "error")
    if sym in syms:
        return render_dashboard(f"Hata: {sym} zaten listede.", "error")
    ok, msg = save_symbols(syms + [sym])
    if ok:
        if bot_running():
            msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
        slog.log_event(slog.COIN_ADD,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip(),
                       detail=sym)
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/coins/delete")
def delete_coin():
    sym = normalize_symbol(request.form.get("symbol"))
    cfg, err = load_config()
    if err or cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    syms = [str(s) for s in cfg.get("symbols", [])]
    if sym is None or sym not in syms:
        return render_dashboard("Hata: Silinecek coin bulunamadı.", "error")
    if len(syms) <= 1:
        return render_dashboard("Hata: En az bir coin kalmalıdır.", "error")
    ok, msg = save_symbols([s for s in syms if s != sym])
    if ok:
        if bot_running():
            msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
        slog.log_event(slog.COIN_DEL,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip(),
                       detail=sym)
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/coins/move")
def move_coin():
    sym       = normalize_symbol(request.form.get("symbol"))
    direction = request.form.get("direction")
    cfg, err  = load_config()
    if err or cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    syms = [str(s) for s in cfg.get("symbols", [])]
    if sym not in syms or direction not in {"up", "down"}:
        return render_dashboard("Hata: Coin sıralaması değiştirilemedi.", "error")
    idx     = syms.index(sym)
    new_idx = idx - 1 if direction == "up" else idx + 1
    if new_idx < 0 or new_idx >= len(syms):
        return render_dashboard("Coin zaten listenin sınırında.", "error")
    syms[idx], syms[new_idx] = syms[new_idx], syms[idx]
    ok, msg = save_symbols(syms)
    if ok and bot_running():
        msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/coins/preset")
def apply_preset():
    name = request.form.get("preset")
    syms = DEFAULT_PRESETS.get(name or "")
    if syms is None:
        return render_dashboard("Hata: Geçersiz hazır liste.", "error")
    ok, msg = save_symbols(list(syms))
    if ok:
        msg = "Hazır coin listesi kaydedildi."
        if bot_running():
            msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/bot/start")
def bot_start():
    # BOTTLENECK NOTE: start_bot() calls subprocess.Popen() which returns
    # immediately — it does NOT wait for the bot process to initialise.
    # Worker hold-time is typically < 100 ms. If BOT_PATH is on a slow
    # filesystem the open() call can add a few hundred ms, but this is
    # well within the gunicorn timeout. No threading change is needed.
    ok, msg = start_bot()
    if ok:
        slog.log_event(slog.BOT_START,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip())
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/bot/stop")
def bot_stop():
    # BOTTLENECK NOTE: stop_bot() sends SIGTERM then polls /proc/<pid> for
    # up to 2 s (20 × 0.1 s) before escalating to SIGKILL. Worst-case
    # worker hold-time is ~2 s — acceptable, but concurrent stop requests
    # would each hold a worker for 2 s. The CONFIG_LOCK prevents concurrent
    # execution, so only one stop call runs at a time; the second caller
    # blocks on the lock and returns quickly ("bot not found").
    ok, msg = stop_bot()
    if ok:
        slog.log_event(slog.BOT_STOP,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip())
    return render_dashboard(msg, "success" if ok else "error")


# ══════════════════════════════════════════════════════════════════════════════
# Rotalar — Akıllı Coin Seçimi
# ══════════════════════════════════════════════════════════════════════════════

VALID_SMART_MODES = {"MANUEL", "ONERI", "OTOMATIK"}
SMART_SETTING_RULES: dict[str, tuple[str, float, float]] = {
    "max_coins":           ("int",   1,   30),
    "min_coins":           ("int",   1,   10),
    "eval_interval_hours": ("int",   1,   168),
    "add_threshold":       ("int",   50,  100),
    "remove_threshold":    ("int",   20,  80),
    "min_hold_hours":      ("int",   1,   168),
    "cooldown_hours":      ("int",   1,   168),
}


@app.post("/smart/mode")
def set_smart_mode():
    mode = (request.form.get("mode") or "").strip().upper()
    if mode not in VALID_SMART_MODES:
        return render_dashboard("Hata: Geçersiz mod.", "error")
    cfg = um.get_smart_config()
    main_cfg, _ = load_config()
    if cfg.get("mode") == "MANUEL" and main_cfg and mode != "MANUEL":
        cfg["manual_list"] = list(main_cfg.get("symbols", []))
    cfg["mode"] = mode
    um.save_smart_config(cfg)
    label = {"MANUEL": "Manuel", "ONERI": "Öneri", "OTOMATIK": "Otomatik"}[mode]
    msg = f"Akıllı seçim modu: {label}."
    if mode == "OTOMATIK":
        msg += " Otomatik analiz saatler içinde devreye girer."
    return render_dashboard(msg, "success")


@app.post("/smart/settings")
def save_smart_settings():
    cfg = um.get_smart_config()
    for name, (kind, lo, hi) in SMART_SETTING_RULES.items():
        raw = request.form.get(name)
        if raw is None:
            continue
        try:
            val: int | float = int(raw.strip()) if kind == "int" else float(raw.strip())
            if not (lo <= val <= hi):
                return render_dashboard(f"Hata: {name} değeri {lo}–{hi} arasında olmalıdır.", "error")
            cfg[name] = val
        except ValueError:
            return render_dashboard(f"Hata: {name} geçersiz değer.", "error")
    anchor = normalize_symbol(request.form.get("anchor_symbol"))
    if anchor:
        cfg["anchor_symbol"] = anchor
    um.save_smart_config(cfg)
    return render_dashboard("Akıllı seçim ayarları kaydedildi.", "success")


@app.post("/smart/analyze")
def trigger_analyze():
    if um.analysis_status["running"] or um.get_smart_config().get("analysis_running"):
        return render_dashboard("Analiz zaten çalışıyor.", "error")
    main_cfg, _ = load_config()
    current   = list((main_cfg or DEFAULT_CONFIG).get("symbols", []))
    smart_cfg = um.get_smart_config()
    started   = um.trigger_analysis(current, smart_cfg, apply_if_auto=False)
    if started:
        return render_dashboard("Analiz başlatıldı. Sonuçlar birkaç dakika içinde görünür.", "success")
    return render_dashboard("Analiz başlatılamadı.", "error")


@app.post("/smart/apply")
def apply_smart_suggestions():
    smart_cfg   = um.get_smart_config()
    suggestions = smart_cfg.get("last_suggestions", [])
    if not suggestions:
        return render_dashboard("Uygulanacak öneri bulunamadı. Önce analiz çalıştırın.", "error")
    main_cfg, err = load_config()
    if err or main_cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    current     = list(main_cfg.get("symbols", []))
    to_add, to_remove = um.compute_auto_changes(suggestions, current, smart_cfg)
    if not to_add and not to_remove:
        return render_dashboard("Değişiklik gerekmedi.", "success")
    ok, msg = um.apply_auto_changes(to_add, to_remove, smart_cfg, smart_cfg.get("mode", "ONERI"))
    um.save_smart_config(smart_cfg)
    if ok and bot_running():
        msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/smart/restore")
def restore_manual_list():
    smart_cfg   = um.get_smart_config()
    manual_list = smart_cfg.get("manual_list")
    if not manual_list:
        return render_dashboard("Kaydedilmiş manuel liste bulunamadı.", "error")
    ok, msg = save_symbols(list(manual_list))
    if ok:
        msg = "Manuel coin listesi geri yüklendi."
        if bot_running():
            msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/smart/pin")
def toggle_pin():
    sym = normalize_symbol(request.form.get("symbol"))
    if sym is None:
        return render_dashboard("Geçersiz sembol.", "error")
    smart_cfg = um.get_smart_config()
    pinned    = list(smart_cfg.get("pinned", []))
    if sym in pinned:
        pinned.remove(sym)
        msg = f"{sym} sabitlemesi kaldırıldı."
    else:
        pinned.append(sym)
        msg = f"{sym} sabitlendi."
    smart_cfg["pinned"] = pinned
    um.save_smart_config(smart_cfg)
    return render_dashboard(msg, "success")


@app.post("/smart/coin-action")
def smart_coin_action():
    sym    = normalize_symbol(request.form.get("symbol"))
    action = (request.form.get("action") or "").strip().lower()
    if sym is None or action not in {"add", "remove"}:
        return render_dashboard("Geçersiz sembol veya işlem.", "error")
    cfg, err = load_config()
    if err or cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    syms = [str(s) for s in cfg.get("symbols", [])]
    if action == "add":
        if sym in syms:
            return render_dashboard(f"{sym} zaten listede.", "error")
        ok, msg = save_symbols(syms + [sym])
    else:
        if sym not in syms:
            return render_dashboard(f"{sym} listede bulunamadı.", "error")
        if len(syms) <= 1:
            return render_dashboard("En az bir coin kalmalıdır.", "error")
        ok, msg = save_symbols([s for s in syms if s != sym])
    if ok and bot_running():
        msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


# ══════════════════════════════════════════════════════════════════════════════
# Rotalar — Uyarlanabilir Motor
# ══════════════════════════════════════════════════════════════════════════════

VALID_ADAPTIVE_MODES = {"MONITOR", "SUGGEST", "AUTO", "SAFE"}


@app.post("/adaptive/enable")
def adaptive_enable():
    enabled = request.form.get("enabled") == "1"
    ok, msg = update_config({"adaptive_system": {
        **(_get_adaptive_cfg()), "enabled": enabled
    }})
    if ok:
        if enabled:
            ac.start_controller_loop()
            msg = "Uyarlanabilir motor etkinleştirildi."
        else:
            ac.stop_controller_loop()
            msg = "Uyarlanabilir motor devre dışı bırakıldı."
    ms.append_system_error(component="adaptive", error_type="ENABLE_CHANGE",
                           message=f"enabled={enabled}") if ok else None
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/adaptive/mode")
def set_adaptive_mode():
    mode = (request.form.get("mode") or "").strip().upper()
    if mode not in VALID_ADAPTIVE_MODES:
        return render_dashboard("Hata: Geçersiz çalışma modu.", "error")
    cfg = _get_adaptive_cfg()
    cfg["mode"] = mode
    ok, msg = _save_adaptive_cfg(cfg)
    mode_label = {"MONITOR": "İzleme", "SUGGEST": "Öneri",
                  "AUTO": "Otomatik PAPER", "SAFE": "Güvenli Durum"}[mode]
    ms.append_system_error(component="adaptive", error_type="MODE_CHANGE",
                           message=f"Mod değiştirildi: {mode}") if ok else None
    return render_dashboard(f"Çalışma modu: {mode_label}.", "success" if ok else "error")


@app.post("/adaptive/settings")
def save_adaptive_settings():
    cfg = _get_adaptive_cfg()
    for name, (kind, lo, hi) in ADAPTIVE_SETTING_RULES.items():
        raw = request.form.get(name)
        if raw is None:
            continue
        parsed = parse_setting(name, raw, ADAPTIVE_SETTING_RULES)
        if parsed is None:
            return render_dashboard(f"Hata: {name} değeri {lo}–{hi} arasında olmalıdır.", "error")
        cfg[name] = parsed
    # Boolean toggles
    for flag in ("break_even_enabled", "trailing_stop_enabled",
                 "partial_take_profit_enabled", "learning_enabled"):
        cfg[flag] = request.form.get(flag) == "1"
    ok, msg = _save_adaptive_cfg(cfg)
    ms.append_system_error(component="adaptive", error_type="SETTINGS_CHANGE",
                           message="Adaptive ayarlar güncellendi.") if ok else None
    return render_dashboard("Uyarlanabilir motor ayarları kaydedildi." if ok else msg,
                            "success" if ok else "error")


@app.post("/adaptive/auto-paper")
def toggle_auto_paper():
    cfg     = _get_adaptive_cfg()
    enabled = request.form.get("enabled") == "1"
    if enabled and not cfg.get("enabled", False):
        return render_dashboard("Otomatik PAPER açmak için önce uyarlanabilir motoru etkinleştirin.", "error")
    cfg["auto_paper_enabled"] = enabled
    ok, msg = _save_adaptive_cfg(cfg)
    label   = "Otomatik PAPER açıldı." if enabled else "Otomatik PAPER kapatıldı."
    ms.append_system_error(component="adaptive", error_type="AUTO_PAPER_TOGGLE",
                           message=label) if ok else None
    return render_dashboard(label if ok else msg, "success" if ok else "error")


@app.post("/adaptive/kill-switch")
def kill_switch():
    activate = request.form.get("activate") == "1"
    if activate:
        sg.activate_kill_switch("Panelden kullanıcı etkinleştirdi.")
        cfg = _get_adaptive_cfg()
        cfg["kill_switch"] = True
        _save_adaptive_cfg(cfg)
        slog.log_event(slog.KILL_SWITCH,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip(),
                       detail="activated")
        return render_dashboard("⛔ Acil durdur etkinleştirildi. Yeni işlem açılmayacak.", "error")
    else:
        sg.deactivate_kill_switch()
        cfg = _get_adaptive_cfg()
        cfg["kill_switch"] = False
        _save_adaptive_cfg(cfg)
        slog.log_event(slog.KILL_SWITCH,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip(),
                       detail="deactivated")
        return render_dashboard("Acil durdur devre dışı bırakıldı.", "success")


@app.post("/execution/mode")
def set_execution_mode():
    """Yürütme modu seçimi — LIVE fail-closed reddedilir."""
    requested = (request.form.get("execution_mode") or "").strip().upper().replace(" ", "_")
    if requested == "LIVE":
        slog.log_event(slog.UNAUTHORIZED_API,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip(),
                       detail="LIVE modu talebi reddedildi (fail-closed kilit)")
        return render_dashboard(
            "⛔ LIVE modu kilitli (fail-closed). Sistem sertifikasyonu yalnızca "
            "PAPER / SHADOW / MICRO LIVE (yetkilendirme) kapsar.", "error")
    if requested not in EXECUTION_MODES:
        return render_dashboard("Geçersiz yürütme modu.", "error")
    with CONFIG_LOCK:
        cfg, err = load_config()
        if err or cfg is None:
            return render_dashboard(f"Ayar dosyası okunamadı: {err}", "error")
        cfg["execution_mode"] = requested
        # Bot çekirdeği için PAPER kilidi her durumda korunur.
        cfg["mode"] = "PAPER"
        try:
            atomic_write_json(CONFIG_PATH, cfg)
        except OSError as exc:
            return render_dashboard(f"Mod kaydedilemedi: {exc}", "error")
    slog.log_event(slog.SETTINGS_CHANGE,
                   username=session.get("username", ""),
                   ip=auth.get_client_ip(),
                   detail=f"execution_mode={requested}")
    label = EXECUTION_MODE_LABELS.get(requested, requested)
    note = " (yalnızca yetkilendirme talebi — borsaya emir yazılmaz)" \
        if requested == "MICRO_LIVE" else ""
    return render_dashboard(f"Yürütme modu {label} olarak ayarlandı.{note}", "success")


@app.post("/adaptive/unlock")
def unlock_safety():
    sg.unlock_safety()
    return render_dashboard("Güvenlik kilidi açıldı.", "success")


@app.post("/adaptive/learn-now")
def trigger_learning():
    cfg = _get_adaptive_cfg()
    try:
        result = le.run_learning_update(cfg, force=True)
        if result:
            return render_dashboard(
                f"Öğrenme güncellendi. Sürüm: {result.get('version', '?')}  "
                f"({result.get('trade_count', 0)} işlem, {result.get('confidence', '—')}).",
                "success")
        return render_dashboard("Öğrenme için yeterli veri yok.", "error")
    except Exception as exc:
        return render_dashboard(f"Öğrenme hatası: {str(exc)[:100]}", "error")


# ══════════════════════════════════════════════════════════════════════════════
# API uç noktaları
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/status")
def api_status():
    status, err = build_status()
    resp = dict(status)
    resp["error"] = err
    resp.pop("trades", None)
    # Kalan süreyi yenilemeden ÖNCE ölç; böylece frontend uyarıyı görebilir.
    # Ardından gerekiyorsa oturumu uzat (son 1 saat içindeyse).
    pre_refresh_remaining = auth.get_session_remaining_seconds()
    auth.maybe_refresh_session()
    resp["session_remaining_seconds"] = pre_refresh_remaining
    return resp


@app.get("/api/smart/status")
def api_smart_status():
    smart_cfg = um.get_smart_config()
    return {
        "running":         smart_cfg.get("analysis_running", False) or um.analysis_status["running"],
        "mode":            smart_cfg.get("mode", "MANUEL"),
        "last_analysis":   smart_cfg.get("last_analysis_time"),
        "candidate_count": smart_cfg.get("candidate_count", 0),
        "next_analysis":   um.next_analysis_str(smart_cfg),
        "error":           um.analysis_status.get("error"),
    }


@app.get("/api/regime")
def api_regime():
    panel = ms.read_panel_status()
    regime = panel.get("market_regime", {})
    if not regime:
        cfg, _ = load_config()
        symbols = (cfg or DEFAULT_CONFIG).get("symbols", ["BTCUSDT"])
        try:
            import market_regime as mr
            regime = mr.detect_market_regime(symbols)
        except Exception as exc:
            return {"error": str(exc)}, 500
    return regime


@app.get("/api/risk")
def api_risk():
    state, _  = read_json(STATE_PATH)
    cfg, _    = load_config()
    adaptive  = (cfg or {}).get("adaptive_system", {})
    if not state:
        return {"error": "state.json okunamadı"}, 500
    import adaptive_risk as ar
    risk_panel = ar.get_risk_panel(state, adaptive)
    return risk_panel


@app.get("/api/decisions")
def api_decisions():
    return {"decisions": ms.get_recent_decisions(20)}


@app.get("/api/learning")
def api_learning():
    try:
        return le.get_panel_data()
    except Exception as exc:
        return {"error": str(exc)}, 500


@app.get("/api/adaptive/status")
def api_adaptive_status():
    ctrl    = ac.get_status()
    safety  = sg.get_safety_state()
    panel   = ms.read_panel_status()
    return {
        "controller": ctrl,
        "safety":     safety,
        "panel":      {k: v for k, v in panel.items() if k != "last_decisions"},
    }


# ── Mission 1400.1: v1 API uç noktaları ──────────────────────────────────────

@app.get("/api/v1/health")
def api_v1_health():
    """Güvenli sağlık yanıtı — kimlik doğrulaması gerektirmez."""
    from version import get_version
    import alpha_platform as ap
    return jsonify(ap.health_payload(get_version()))


@app.post("/api/v1/auth/login")
@csrf.exempt
def api_v1_login():
    """JSON tabanlı sahip girişi. Oturum yokken CSRF token'ı olamayacağı
    için muaftır; hız sınırı ve genel hata mesajı uygulanır."""
    if not auth.password_hash_configured():
        slog.log_event(slog.APP_LOCKED, ip=auth.get_client_ip(),
                       detail=f"rid={g.request_id}")
        return _api_error("Kurulum kilitli.", 403)
    ip = auth.get_client_ip()
    allowed, secs = auth.check_rate_limit(ip)
    if not allowed:
        slog.log_event(slog.LOGIN_FAIL, detail=f"rid={g.request_id} rate", ip=ip)
        return _api_error(f"Çok fazla deneme. {secs} saniye sonra tekrar "
                          "deneyin.", 429)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _api_error("Bozuk istek gövdesi.", 400)
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or not password:
        auth.record_attempt(ip, success=False)
        return _api_error("Kullanıcı adı veya parola hatalı.", 401)
    if auth.verify_credentials(username, password):
        auth.record_attempt(ip, success=True)
        session.clear()                       # oturum sabitleme önlemi
        auth.start_session(username)
        slog.log_event(slog.LOGIN_OK, username=username, ip=ip,
                       detail=f"rid={g.request_id}")
        return jsonify({"ok": True, "request_id": g.request_id})
    auth.record_attempt(ip, success=False)
    slog.log_event(slog.LOGIN_FAIL, ip=ip,
                   detail=f"rid={g.request_id} user={username[:20]}")
    return _api_error("Kullanıcı adı veya parola hatalı.", 401)


@app.post("/api/v1/auth/logout")
def api_v1_logout():
    username = auth.clear_session()
    slog.log_event(slog.LOGOUT, username=username, ip=auth.get_client_ip(),
                   detail=f"rid={g.request_id}")
    return jsonify({"ok": True})


@app.get("/api/v1/auth/session")
def api_v1_session():
    return jsonify({
        "authenticated": bool(session.get("logged_in")),
        "username": session.get("username", ""),
        "remaining_seconds": auth.get_session_remaining_seconds(),
    })


@app.get("/api/v1/application/config")
def api_v1_application_config():
    from version import get_version
    import alpha_platform as ap
    resp = jsonify(ap.application_config(
        get_version(), session.get("username", "")))
    resp.headers["Cache-Control"] = "no-store, private"
    return resp


# ── Mission 1400.2 — Salt-okunur canlı pano API'leri ────────────────────────
# Tüm rotalar güvenlik kapısından geçer (sahip oturumu zorunlu).

def _dashboard_app_info() -> dict:
    from version import get_version
    import alpha_platform as ap
    flags = ap.feature_flags()
    return {
        "version": get_version(),
        "mode": ap.app_mode(),
        "setup_state": ap.setup_state(),
        "dry_run_enabled": flags.get("ALPHA_ENABLE_DRY_RUN", False),
    }


def _dash_json(payload: dict, status: int = 200):
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store, private"
    return resp, status


@app.get("/api/v1/overview")
def api_v1_overview():
    import dashboard_api as dapi
    return _dash_json(dapi.overview(_dashboard_app_info()))


@app.get("/api/v1/tr/account")
def api_v1_tr_account():
    import dashboard_api as dapi
    return _dash_json(dapi.tr_account())


@app.get("/api/v1/tr/movements/summary")
def api_v1_tr_movements_summary():
    import dashboard_api as dapi
    return _dash_json(dapi.tr_movements_summary())


@app.get("/api/v1/system/status")
def api_v1_system_status():
    import dashboard_api as dapi
    return _dash_json(dapi.system_status(_dashboard_app_info()))


@app.post("/api/v1/refresh")
def api_v1_refresh():
    """Uygulama-yerel yenileme: yalnızca güvenli okuma önbelleklerini
    temizler ve yeni GET istekleri başlatır. CSRF korumalıdır (Flask-WTF
    POST'ları otomatik doğrular). Hiçbir borsa yazma ucu çağrılmaz."""
    import dashboard_api as dapi
    import ledger_api as la
    cleared = dapi.invalidate_caches()
    cleared += la.invalidate_ledger_caches()   # defter/denetim önbellekleri
    ip = auth.get_client_ip()
    slog.log_event(slog.STARTUP, ip=ip,
                   detail=f"manual refresh: {len(cleared)} cache cleared")
    data = dapi.overview(_dashboard_app_info())
    ts = dapi.mark_full_refresh()
    data["last_full_refresh"] = ts
    slog.log_event(slog.STARTUP, ip=ip, detail="manual refresh completed")
    return _dash_json({"ok": True, "refreshed_at": ts, "overview": data})


@app.get("/overview")
def overview_page():
    """Genel Bakış — tek birincil pano deneyimi (Mission 1400.2)."""
    from version import get_version
    import alpha_platform as ap
    return render_template(
        "overview.html",
        app_mode=ap.app_mode(),
        app_version=get_version(),
        setup_state=ap.setup_state(),
        owner=session.get("username", ""),
        server_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        flags=ap.feature_flags(),
    )


# ── Mission 1400.3 — Portföy / Pozisyonlar / Emirler (salt okunur) ──────────

def _pf_params():
    """Ortak sorgu parametresi doğrulaması (geçersiz → InvalidParameter)."""
    import portfolio_api as pf
    return {
        "include_zero": pf._parse_bool(request.args.get("include_zero"),
                                       default=False),
        "search": pf._parse_search(request.args.get("search")),
        "sort": pf._parse_enum(request.args.get("sort"),
                               set(pf.PORTFOLIO_SORTS), "sort"),
        "order": pf._parse_enum(request.args.get("order"), {"asc", "desc"},
                                "order", "asc"),
        "limit": pf._parse_limit(request.args.get("limit")),
    }


def _invalid_param(name: str):
    return jsonify({
        "ok": False,
        "error": {"code": "INVALID_PARAMETER",
                  "message": f"Geçersiz sorgu parametresi: {name}"},
        "request_id": getattr(g, "request_id", ""),
    }), 400


@app.get("/api/v1/portfolio")
def api_v1_portfolio():
    import portfolio_api as pf
    import alpha_platform as ap
    try:
        p = _pf_params()
    except pf.InvalidParameter as e:
        return _invalid_param(e.name)
    return _dash_json(pf.portfolio(ap.app_mode(), **p))


@app.get("/api/v1/portfolio/export.csv")
def api_v1_portfolio_export():
    import portfolio_api as pf
    try:
        p = _pf_params()
    except pf.InvalidParameter as e:
        return _invalid_param(e.name)
    try:
        body, fname = pf.portfolio_csv(**p)
    except Exception:
        return _api_error("CSV üretimi başarısız oldu.", 500)
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   detail=f"csv export: portfolio rows<= {p['limit']}")
    return app.response_class(
        body, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fname}",
                 "Cache-Control": "no-store, private"})


def _render_workspace(template: str, page: str):
    from version import get_version
    import alpha_platform as ap
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   detail=f"page opened: {page}")
    return render_template(
        template,
        app_mode=ap.app_mode(),
        app_version=get_version(),
        owner=session.get("username", ""),
        active_page=page,
    )


@app.get("/portfolio")
def portfolio_page():
    return _render_workspace("portfolio.html", "portfolio")


# ── Mission 1400.4 — Defter / Denetim / Raporlar (salt okunur) ──────────────

# ── Mission 1400.6 — Risk Intelligence Engine (salt-okunur, tavsiye) ────────

def _risk_json(fn):
    try:
        return jsonify(fn())
    except Exception:
        app.logger.exception("risk api hatası")
        return jsonify({"ok": False, "error": {
            "code": "RISK_ENGINE_ERROR",
            "message": "Risk hesabı tamamlanamadı."}}), 500


@app.get("/risk")
def risk_page():
    return _render_workspace("risk.html", "risk")


@app.get("/api/v1/risk/summary")
@app.get("/api/risk/summary")
def api_risk_summary():
    import risk_api as ra
    return _risk_json(ra.summary)


@app.get("/api/v1/risk/exposure")
@app.get("/api/risk/exposure")
def api_risk_exposure():
    import risk_api as ra
    return _risk_json(ra.exposure)


@app.get("/api/v1/risk/alerts")
@app.get("/api/risk/alerts")
def api_risk_alerts():
    import risk_api as ra
    return _risk_json(ra.alerts)


@app.get("/api/v1/risk/history")
@app.get("/api/risk/history")
def api_risk_history():
    import risk_api as ra
    return _risk_json(ra.history)


def _run_simulator(params: dict):
    """Salt-okunur YEREL simülasyon — borsaya HİÇBİR istek atılmaz."""
    import risk_api as ra
    try:
        return jsonify(ra.simulate(params))
    except ValueError as exc:
        return jsonify({"ok": False, "error": {
            "code": "INVALID_PARAMETER", "message": str(exc)}}), 400
    except Exception:
        app.logger.exception("simülatör hatası")
        return jsonify({"ok": False, "error": {
            "code": "RISK_ENGINE_ERROR",
            "message": "Simülasyon tamamlanamadı."}}), 500


@app.post("/api/v1/risk/simulator")
def api_risk_simulator_post():
    """Spec 6.8: POST — yalnızca yerel hesap; CSRF korumalı, borsa yok."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": {
            "code": "INVALID_PARAMETER",
            "message": "JSON gövde bekleniyor."}}), 400
    return _run_simulator(body)


@app.get("/api/risk/simulator")
def api_risk_simulator():
    return _run_simulator(request.args.to_dict())


# ── Mission 1500.1 / Agent 07 — Read-Only Intelligence API ──────────────────
# YALNIZCA GET. Kimlik doğrulama _security_gate ile zorunludur; oturum/
# giriş denemeleri mevcut rate-limit modeliyle sınırlıdır. Tüm yanıtlar
# no-store'dur; hata yanıtları sterilizedir (stack trace / secret yok).

_intel_service = None


def _get_intel_service():
    global _intel_service
    if _intel_service is None:
        from intelligence_service import IntelligenceService
        _intel_service = IntelligenceService()
    return _intel_service


def _intel_enabled() -> bool:
    import intelligence_settings
    return intelligence_settings.get_settings()["enabled"]


def _intel_json(fn):
    if not _intel_enabled():
        # Feature flag kapalı: güvenli, verisiz yanıt (veri uydurulmaz)
        resp = jsonify({"ok": True, "enabled": False, "read_only": True,
                        "advisory_only": True, "status": "UNAVAILABLE",
                        "message": "Intelligence özelliği kapalı "
                                   "(ALPHA_INTELLIGENCE_ENABLED)."})
        resp.headers["Cache-Control"] = "no-store, private"
        return resp
    try:
        payload = fn()
    except Exception:
        app.logger.exception("intelligence api hatası")
        resp = jsonify({"ok": False, "error": {
            "code": "INTELLIGENCE_ERROR",
            "message": "Intelligence çıktısı üretilemedi."}})
        resp.headers["Cache-Control"] = "no-store, private"
        return resp, 500
    if isinstance(payload, list):
        payload = {"ok": True, "read_only": True, "advisory_only": True,
                   "enabled": True, "items": payload}
    else:
        payload = {**payload, "enabled": True}
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store, private"
    return resp


@app.get("/intelligence")
def intelligence_page():
    return _render_workspace("intelligence.html", "intelligence")


@app.get("/api/intelligence")
@app.get("/api/v1/intelligence")
@app.get("/api/intelligence/summary")
@app.get("/api/v1/intelligence/summary")
def api_intelligence_summary():
    return _intel_json(lambda: _get_intel_service().get_summary())


@app.get("/api/intelligence/insights")
@app.get("/api/v1/intelligence/insights")
def api_intelligence_insights():
    return _intel_json(lambda: _get_intel_service().get_insights())


@app.get("/api/intelligence/recommendations")
@app.get("/api/v1/intelligence/recommendations")
def api_intelligence_recommendations():
    return _intel_json(lambda: _get_intel_service().get_recommendations())


@app.get("/api/intelligence/status")
@app.get("/api/v1/intelligence/status")
def api_intelligence_status():
    return _intel_json(lambda: _get_intel_service().get_status())


@app.get("/api/intelligence/settings")
@app.get("/api/v1/intelligence/settings")
def api_intelligence_settings():
    """Etkili (doğrulanmış) Intelligence yapılandırması — salt-okunur.

    Ham ortam değişkeni değerleri asla döndürülmez; yalnızca türetilmiş
    etkili değerler ve doğrulama uyarı kodları gösterilir. Bayrak kapalı
    olsa da operatörün nedenini görebilmesi için bu uç yanıt verir.
    """
    import intelligence_settings
    s = intelligence_settings.get_settings()
    resp = jsonify({"ok": True, "read_only": True, "advisory_only": True,
                    "settings": s})
    resp.headers["Cache-Control"] = "no-store, private"
    return resp


# ── Mission 1600 / Agent 04: Automation Management API ──────────────
# Durum: GET (sterile, read-only). Tetik: POST (CSRFProtect + _security_gate).
# Route katmanı incedir: iş mantığı automation_service/automation_engine'de
# kalır; append_snapshot ve IntelligenceService'e doğrudan erişilmez.
# Enable/disable uçları BİLİNÇLİ olarak yok: yapılandırma ortam tabanlıdır
# (ALPHA_AUTOMATION_ENABLED), kalıcı runtime config modeli bulunmuyor.

_automation_thread = None
_automation_thread_lock = threading.Lock()


def start_automation_scheduler():
    """Automation zamanlayıcısını güvenli biçimde başlatır (post_fork).

    - Varsayılan KAPALI: yalnız ALPHA_AUTOMATION_ENABLED literal "true"
      ise loop başlar.
    - Aynı process'te ikinci loop başlatılmaz (kilitli tekil guard).
    - Başlatma hatası uygulamayı asla çökertmez (sterile log).
    - Süreçler-arası duplicate execution yine Core'daki flock ile önlenir
      (her worker'da bir loop çalışır; tek koşu garantisi kilittedir).
    """
    global _automation_thread
    try:
        import automation_engine
        import automation_service
        if not automation_engine.load_config()["enabled"]:
            return None
        with _automation_thread_lock:
            if _automation_thread is not None and _automation_thread.is_alive():
                return _automation_thread
            _automation_thread = automation_engine.start_loop(
                automation_service.build_summary_provider())
            return _automation_thread
    except Exception:
        app.logger.exception("automation scheduler başlatılamadı")
        return None


def _automation_json(payload: dict, status: int = 200):
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store, private"
    return (resp, status) if status != 200 else resp


@app.get("/automation")
def automation_page():
    return _render_workspace("automation.html", "automation")


@app.get("/api/automation/status")
@app.get("/api/v1/automation/status")
def api_automation_status():
    import automation_engine as _ae
    cfg = _ae.load_config()
    st = _ae.load_state()
    next_due = None
    if cfg["enabled"] and st.get("last_run_finished_at"):
        epoch = _ae._epoch_of(st["last_run_finished_at"])
        if epoch is not None:
            next_due = datetime.fromtimestamp(
                epoch + cfg["interval_minutes"] * 60,
                timezone.utc).isoformat()
    return _automation_json({
        "ok": True, "read_only": True, "advisory_only": True,
        "enabled": cfg["enabled"],
        "interval_minutes": cfg["interval_minutes"],
        "state": st["state"],
        "running": st["state"] == "running",
        "run_id": st["run_id"],
        "last_run_started_at": st["last_run_started_at"],
        "last_run_finished_at": st["last_run_finished_at"],
        "last_run_status": st["last_run_status"],
        "last_error_code": st["last_error_code"],
        "last_snapshot_recorded": st["last_snapshot_recorded"],
        "next_due": next_due})


@app.post("/api/automation/run")
@app.post("/api/v1/automation/run")
def api_automation_run():
    import automation_engine as _ae
    import automation_service as _asv
    cfg = _ae.load_config()
    if not cfg["enabled"]:
        return _automation_json({"ok": False, "error": {
            "code": "AUTOMATION_DISABLED",
            "message": "Automation kapalı (ALPHA_AUTOMATION_ENABLED)."}}, 503)
    try:
        out = _asv.run_automation(config=cfg)
    except Exception:
        app.logger.exception("automation run hatası")
        return _automation_json({"ok": False, "error": {
            "code": "AUTOMATION_ERROR",
            "message": "Automation çalıştırılamadı."}}, 500)
    if out.get("skip_reason") == "DUPLICATE_RUN":
        return _automation_json({"ok": False, "error": {
            "code": "DUPLICATE_RUN",
            "message": "Automation zaten çalışıyor."}}, 409)
    return _automation_json({
        "ok": True, "read_only": True, "advisory_only": True,
        "ran": bool(out.get("ran")),
        "appended": bool(out.get("appended")),
        "error_code": out.get("error_code"),
        "final_state": out.get("final_state"),
        "run_id": out.get("run_id")})


# ── Mission 1600 / Agent 06: Automation Export ──────────────────────
# YALNIZCA GET. Veri tek kaynaktan gelir: automation_export_api →
# automation_engine durum okuma sözleşmesi. Koşu başlatılmaz, snapshot
# yazılmaz. History modeli yoktur; history export bilinçli olarak YOK.

def _aex_response(result):
    env, body, mime, filename = result
    if body is None:  # sterile zarf — kaynak okunamadı
        return _automation_json(env, 503)
    resp = app.response_class(body, mimetype=mime)
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["Content-Disposition"] = \
        f'attachment; filename="{filename}"'
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.get("/api/automation/export/status")
@app.get("/api/v1/automation/export/status")
def api_automation_export_status():
    import automation_export_api as aex
    raw = (request.args.get("format") or "json").strip().lower()
    if raw not in aex.FORMATS:
        return _automation_json({"ok": False, "error": {
            "code": "INVALID_FORMAT",
            "message": "Geçersiz format parametresi."}}, 400)
    return _aex_response(aex.export_status(raw))


# ── Mission 1700 / Agent 04: Portfolio Intelligence API ─────────────
# YALNIZCA GET. Rota hesap YAPMAZ: portfolio_service → portfolio çekirdeği
# zinciri tek otoritedir. generated_at yalnız bu API sınırında üretilir
# (Core/Service duvar saati okumaz). İstemciden generated_at override
# kabul edilmez. Kimlik doğrulama _security_gate ile zorunludur.

@app.get("/portfolio-intelligence")
def portfolio_intelligence_page():
    return _render_workspace("portfolio_intelligence.html",
                             "portfolio_intelligence")


@app.get("/api/portfolio/intelligence")
@app.get("/api/v1/portfolio/intelligence")
def api_portfolio_intelligence():
    import portfolio_service as _psv
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        envelope = _psv.get_portfolio_analysis(
            _psv.build_default_providers(), generated_at)
    except Exception:
        app.logger.exception("portfolio intelligence hatası")
        return _automation_json({"ok": False, "error": {
            "code": "PORTFOLIO_ANALYSIS_ERROR",
            "message": "Portföy analizi üretilemedi."}}, 500)
    return _automation_json(envelope)


# ── Mission 1700 / Agent 06: Portfolio Intelligence Export ──────────
# YALNIZCA GET. Veri tek kaynaktan gelir: Agent 04 ile AYNI kompozisyon
# (servis → çekirdek zarfı); alternatif veri yolu yoktur. Export katmanı
# (portfolio_export) zarfı değiştirmez, dosya yazmaz, bellek içi üretir.

def _portfolio_intelligence_export(fmt: str):
    import portfolio_service as _psv
    import portfolio_export as _pex
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        # Servis + export + yanıt kurulumu tek sterile sınır içinde:
        # hangi aşama patlarsa patlasın istisna metni dışarı sızmaz.
        envelope = _psv.get_portfolio_analysis(
            _psv.build_default_providers(), generated_at)
        return _aex_response(_pex.export_analysis(envelope, fmt))
    except Exception:
        app.logger.exception("portfolio intelligence export hatası")
        return _automation_json({"ok": False, "error": {
            "code": "PORTFOLIO_ANALYSIS_ERROR",
            "message": "Portföy analizi üretilemedi."}}, 500)


@app.get("/api/portfolio/intelligence/export/json")
@app.get("/api/v1/portfolio/intelligence/export/json")
def api_portfolio_intelligence_export_json():
    return _portfolio_intelligence_export("json")


@app.get("/api/portfolio/intelligence/export/csv")
@app.get("/api/v1/portfolio/intelligence/export/csv")
def api_portfolio_intelligence_export_csv():
    return _portfolio_intelligence_export("csv")


# ── Mission 1800 / Agent 05: Strategy Intelligence UI ───────────────
# Salt-okunur sunum sayfası; veri YALNIZ Agent 04 API'sından çekilir.

@app.get("/strategy-intelligence")
def strategy_intelligence_page():
    return _render_workspace("strategy_intelligence.html",
                             "strategy_intelligence")


# ── Mission 1800 / Agent 04: Strategy Intelligence API ──────────────
# YALNIZCA GET. Rota hesap YAPMAZ: strategy_service → strategy çekirdeği
# zinciri tek otoridedir. proposal_id + generated_at YALNIZ bu API
# sınırında üretilir (Core/Service deterministik kalır); istemciden
# override kabul edilmez. Kimlik doğrulama _security_gate ile zorunlu.

@app.get("/api/strategy/intelligence")
@app.get("/api/v1/strategy/intelligence")
def api_strategy_intelligence():
    import strategy_service as _ssv
    try:
        proposal = _ssv.analyze_strategy(
            _ssv.build_default_strategy_providers())
        # İstek-kapsamlı meta yalnız burada eklenir (Agent 01 §3):
        proposal["proposal_id"] = uuid.uuid4().hex
        proposal["generated_at"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        app.logger.exception("strategy intelligence hatası")
        return _automation_json({"ok": False, "error": {
            "code": "STRATEGY_ANALYSIS_ERROR",
            "message": "Strateji önerisi üretilemedi."}}, 500)
    return _automation_json(proposal)


# ── Mission 1500.2: Workspace Read-Only API ──────────────────────────
# YALNIZCA GET. Veri tek kaynaktan gelir: intelligence_workspace_service
# (timeline modülüne doğrudan erişilmez). Kimlik doğrulama _security_gate
# ile zorunludur. Yanıtlar her zaman no-store, private taşır.

def _ws_json(payload, status_code=200):
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store, private"
    return (resp, status_code) if status_code != 200 else resp


def _ws_int(name, default=None, required=False):
    """Sorgu parametresini katı tamsayı olarak çözer; bozuksa ValueError."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        if required:
            raise ValueError(name)
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(name)
    if value < 0:
        raise ValueError(name)
    return value


def _ws_bool(name):
    """'true'/'false' dışındaki değerler geçersizdir (katı çözümleme)."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    low = raw.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    raise ValueError(name)


def _ws_bad_request(param):
    return _ws_json({"ok": False, "error": {
        "code": "INVALID_PARAMETER",
        "message": f"Geçersiz parametre: {param}"}}, 400)


@app.get("/workspace")
def workspace_page():
    # Kimlik doğrulamalı sayfa içeriği önbelleğe alınmaz (Agent 07 bulgusu)
    resp = app.make_response(
        _render_workspace("intelligence_workspace.html", "workspace"))
    resp.headers["Cache-Control"] = "no-store, private"
    return resp


@app.get("/api/workspace/timeline")
@app.get("/api/v1/workspace/timeline")
def api_workspace_timeline():
    import intelligence_workspace_service as wss
    try:
        limit = _ws_int("limit")
        offset = _ws_int("offset", default=0)
    except ValueError as e:
        return _ws_bad_request(str(e))
    return _ws_json(wss.get_timeline(limit=limit, offset=offset))


@app.get("/api/workspace/snapshot/<snapshot_id>")
@app.get("/api/v1/workspace/snapshot/<snapshot_id>")
def api_workspace_snapshot(snapshot_id: str):
    import intelligence_workspace_service as wss
    # Katı id çözümleme: tamsayı olmayan/pozitif olmayan id → 400
    try:
        sid = int(snapshot_id)
    except (TypeError, ValueError):
        return _ws_bad_request("snapshot_id")
    if sid < 1:
        return _ws_bad_request("snapshot_id")
    out = wss.get_snapshot(sid)
    if not out.get("ok") and \
            out.get("error", {}).get("code") == "SNAPSHOT_NOT_FOUND":
        return _ws_json(out, 404)
    return _ws_json(out)


@app.get("/api/workspace/compare")
@app.get("/api/v1/workspace/compare")
def api_workspace_compare():
    import intelligence_workspace_service as wss
    try:
        a = _ws_int("a", required=True)
        b = _ws_int("b", required=True)
    except ValueError as e:
        return _ws_bad_request(str(e))
    if a < 1 or b < 1:
        return _ws_bad_request("a" if a < 1 else "b")
    out = wss.compare_snapshots(a, b)
    if not out.get("ok") and \
            out.get("error", {}).get("code") == "SNAPSHOT_NOT_FOUND":
        return _ws_json(out, 404)
    return _ws_json(out)


@app.get("/api/workspace/recommendations")
@app.get("/api/v1/workspace/recommendations")
def api_workspace_recommendations():
    import intelligence_workspace_service as wss
    return _ws_json(wss.get_recommendation_history())


@app.get("/api/workspace/risk-evolution")
@app.get("/api/v1/workspace/risk-evolution")
def api_workspace_risk_evolution():
    import intelligence_workspace_service as wss
    return _ws_json(wss.get_risk_evolution())


@app.get("/api/workspace/search")
@app.get("/api/v1/workspace/search")
def api_workspace_search():
    import intelligence_workspace_service as wss
    from datetime import datetime as _dt

    def _iso(name, *alts):
        for key in (name, *alts):
            raw = request.args.get(key)
            if raw:
                try:
                    _dt.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(key)
                return raw
        return None

    try:
        partial = _ws_bool("partial")
        advisory = _ws_bool("advisory_only")
        start = _iso("start", "date")
        end = _iso("end", "date_end")
    except ValueError as e:
        return _ws_bad_request(str(e))
    return _ws_json(wss.search(
        start=start,
        end=end,
        status=request.args.get("status") or None,
        confidence=request.args.get("confidence") or None,
        recommendation_code=request.args.get("recommendation") or None,
        insight_code=request.args.get("insight") or None,
        partial=partial,
        advisory_only=advisory,
    ))


# ── Mission 1500.2: Workspace Export (Agent 06) ──────────────────────
# YALNIZCA GET. Veri tek kaynaktan gelir: workspace_export_api →
# intelligence_workspace_service. Salt-okunur; hiçbir kayıt yazılmaz.

def _wsx_format():
    raw = (request.args.get("format") or "json").strip().lower()
    import workspace_export_api as wsx
    if raw not in wsx.FORMATS:
        raise ValueError("format")
    return raw


def _wsx_response(result):
    env, body, mime, filename = result
    if body is None:  # sterile servis zarfı — 404 yalnız SNAPSHOT_NOT_FOUND
        code = (env.get("error") or {}).get("code")
        status = 404 if code == "SNAPSHOT_NOT_FOUND" else 200
        return _ws_json(env, status)
    resp = app.response_class(body, mimetype=mime)
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["Content-Disposition"] = \
        f'attachment; filename="{filename}"'
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.get("/api/workspace/export/timeline")
@app.get("/api/v1/workspace/export/timeline")
def api_workspace_export_timeline():
    import workspace_export_api as wsx
    try:
        fmt = _wsx_format()
        limit = _ws_int("limit")
        offset = _ws_int("offset", default=0)
    except ValueError as e:
        return _ws_bad_request(str(e))
    return _wsx_response(wsx.export_timeline(fmt, limit=limit,
                                             offset=offset))


@app.get("/api/workspace/export/snapshot/<snapshot_id>")
@app.get("/api/v1/workspace/export/snapshot/<snapshot_id>")
def api_workspace_export_snapshot(snapshot_id: str):
    import workspace_export_api as wsx
    try:
        fmt = _wsx_format()
    except ValueError as e:
        return _ws_bad_request(str(e))
    try:
        sid = int(snapshot_id)
    except (TypeError, ValueError):
        return _ws_bad_request("snapshot_id")
    if sid < 1:
        return _ws_bad_request("snapshot_id")
    return _wsx_response(wsx.export_snapshot(fmt, sid))


@app.get("/api/workspace/export/compare")
@app.get("/api/v1/workspace/export/compare")
def api_workspace_export_compare():
    import workspace_export_api as wsx
    try:
        fmt = _wsx_format()
        a = _ws_int("a", required=True)
        b = _ws_int("b", required=True)
    except ValueError as e:
        return _ws_bad_request(str(e))
    if a < 1 or b < 1:
        return _ws_bad_request("a" if a < 1 else "b")
    return _wsx_response(wsx.export_compare(fmt, a, b))


@app.get("/api/workspace/export/recommendations")
@app.get("/api/v1/workspace/export/recommendations")
def api_workspace_export_recommendations():
    import workspace_export_api as wsx
    try:
        fmt = _wsx_format()
    except ValueError as e:
        return _ws_bad_request(str(e))
    return _wsx_response(wsx.export_recommendations(fmt))


@app.get("/api/workspace/export/risk-evolution")
@app.get("/api/v1/workspace/export/risk-evolution")
def api_workspace_export_risk_evolution():
    import workspace_export_api as wsx
    try:
        fmt = _wsx_format()
    except ValueError as e:
        return _ws_bad_request(str(e))
    return _wsx_response(wsx.export_risk_evolution(fmt))


@app.get("/api/workspace/export/search")
@app.get("/api/v1/workspace/export/search")
def api_workspace_export_search():
    import workspace_export_api as wsx
    from datetime import datetime as _dt

    def _iso(name, *alts):
        for key in (name, *alts):
            raw = request.args.get(key)
            if raw:
                try:
                    _dt.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(key)
                return raw
        return None

    try:
        fmt = _wsx_format()
        partial = _ws_bool("partial")
        advisory = _ws_bool("advisory_only")
        start = _iso("start", "date")
        end = _iso("end", "date_end")
    except ValueError as e:
        return _ws_bad_request(str(e))
    return _wsx_response(wsx.export_search(
        fmt,
        start=start,
        end=end,
        status=request.args.get("status") or None,
        confidence=request.args.get("confidence") or None,
        recommendation_code=request.args.get("recommendation") or None,
        insight_code=request.args.get("insight") or None,
        partial=partial,
        advisory_only=advisory,
    ))


@app.get("/api/v1/executive/summary")
def api_executive_summary():
    """Mission 1400.5 — yönetici üst çubuğu özeti (salt-okunur)."""
    import alpha_platform as ap
    import executive_api as xa
    return jsonify(xa.executive_summary(bot_is_running=bot_running(),
                                        app_mode=ap.app_mode()))


@app.get("/api/v1/ledger/events")
def api_v1_ledger_events():
    import ledger_api as la
    try:
        f = la.parse_ledger_filters(request.args)
    except la.InvalidParameter as e:
        return _invalid_param(e.name)
    return _dash_json(la.ledger_events(f))


@app.get("/api/v1/ledger/summary")
def api_v1_ledger_summary():
    import ledger_api as la
    return _dash_json(la.ledger_summary())


@app.get("/api/v1/ledger/integrity")
def api_v1_ledger_integrity():
    import ledger_api as la
    result = la.ledger_integrity()
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   detail=f"integrity check: {result['status']}")
    return _dash_json({"ok": result["status"] != "FAIL", **result})


@app.get("/api/v1/ledger/reconciliation")
def api_v1_ledger_reconciliation():
    import ledger_api as la
    return _dash_json(la.ledger_reconciliation())


@app.get("/api/v1/audit/events")
def api_v1_audit_events():
    # Denetim satırlarını getirmek YENİ denetim kaydı üretmez (rekürsiyon yok)
    import ledger_api as la
    try:
        f = la.parse_audit_filters(request.args)
    except la.InvalidParameter as e:
        return _invalid_param(e.name)
    return _dash_json(la.audit_events(f))


@app.get("/api/v1/audit/summary")
def api_v1_audit_summary():
    import ledger_api as la
    d = la.audit_summary()
    d["legacy_env_warnings"] = local_env.legacy_name_warnings()
    return _dash_json(d)


@app.get("/api/v1/reports")
def api_v1_reports():
    import ledger_api as la
    try:
        limit, offset = la.parse_page(request.args, la.REPORTS_DEFAULT_LIMIT,
                                      la.REPORTS_MAX_LIMIT)
    except la.InvalidParameter as e:
        return _invalid_param(e.name)
    return _dash_json(la.reports_list(limit, offset))


@app.get("/api/v1/reports/<report_id>")
def api_v1_report_detail(report_id: str):
    import ledger_api as la
    d = la.report_detail(report_id)
    if d is None:
        return jsonify({"ok": False,
                        "error": {"code": "REPORT_NOT_FOUND",
                                  "message": "Rapor bulunamadı."}}), 404
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   detail=f"report viewed: {report_id}")
    return _dash_json(d)


@app.get("/api/v1/reports/<report_id>/download")
def api_v1_report_download(report_id: str):
    import ledger_api as la
    r = la.report_download(report_id)
    if r is None:
        return jsonify({"ok": False,
                        "error": {"code": "REPORT_NOT_FOUND",
                                  "message": "Rapor bulunamadı."}}), 404
    body, fname = r
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   detail=f"report downloaded: {report_id}")
    return app.response_class(
        body, mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fname}",
                 "Cache-Control": "no-store, private"})


@app.get("/api/v1/ledger/export.csv")
def api_v1_ledger_export():
    import ledger_api as la
    try:
        f = la.parse_ledger_filters(request.args)
    except la.InvalidParameter as e:
        return _invalid_param(e.name)
    try:
        body, fname = la.ledger_csv(f)
    except RuntimeError:
        # Bütünlük FAIL → dışa aktarma kapalı (fail-closed)
        return jsonify({"ok": False,
                        "error": {"code": "LEDGER_INTEGRITY_FAILED",
                                  "message": "Bütünlük doğrulanamadı; dışa "
                                             "aktarma kapalı."}}), 503
    except Exception:
        return _api_error("CSV üretimi başarısız oldu.", 500)
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   detail="csv export: ledger")
    return app.response_class(
        body, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fname}",
                 "Cache-Control": "no-store, private"})


@app.get("/api/v1/audit/export.csv")
def api_v1_audit_export():
    import ledger_api as la
    try:
        f = la.parse_audit_filters(request.args)
    except la.InvalidParameter as e:
        return _invalid_param(e.name)
    try:
        body, fname = la.audit_csv(f)
    except RuntimeError:
        return jsonify({"ok": False,
                        "error": {"code": "AUDIT_UNAVAILABLE",
                                  "message": "Denetim günlüğü okunamadı."
                                  }}), 503
    except Exception:
        return _api_error("CSV üretimi başarısız oldu.", 500)
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   detail="csv export: audit")
    return app.response_class(
        body, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={fname}",
                 "Cache-Control": "no-store, private"})


@app.get("/ledger")
def ledger_page():
    return _render_workspace("ledger.html", "ledger")


@app.get("/audit")
def audit_page():
    return _render_workspace("audit.html", "audit")


@app.get("/reports")
def reports_page():
    return _render_workspace("reports.html", "reports")


@app.get("/api/exchange/summary")
def api_exchange_summary():
    """Salt-okunur borsa özeti (Mission 1400). Tüm borsa istekleri
    backend'den çıkar; secret'lar asla yanıtta yer almaz."""
    import exchange_gateway as xg
    resp = jsonify(xg.exchange_summary())
    resp.headers["Cache-Control"] = "no-store, private"
    return resp


@app.get("/api/daily-report")
def api_daily_report():
    cfg, _ = load_config()
    return _build_daily_report(cfg)


@app.get("/api/daily-report/export")
def export_daily_report():
    # BOTTLENECK NOTE: this route reads state.json (disk I/O) and builds a
    # CSV in memory. For typical paper-trading runs (hundreds of trades) it
    # completes in < 50 ms. With tens of thousands of trades the in-memory
    # StringIO build could take 200–500 ms. If that becomes an issue, switch
    # to a streaming Response with a generator so the worker is released
    # incrementally rather than held for the full build time.
    cfg, _  = load_config()
    report  = _build_daily_report(cfg)
    state, _ = read_json(STATE_PATH)
    trades  = (state or {}).get("trades", []) or []
    today   = datetime.now(timezone.utc).date().isoformat()
    today_t = [t for t in trades if (t.get("closed_at", "") or "").startswith(today)]

    si  = io.StringIO()
    w   = csv.writer(si)
    w.writerow(["symbol","side","opened_at","closed_at","entry","stop","target",
                "quantity","pnl","result","balance_after"])
    for t in today_t:
        w.writerow([
            t.get("symbol",""), t.get("side",""),
            t.get("opened_at",""), t.get("closed_at",""),
            t.get("entry",""), t.get("stop",""), t.get("target",""),
            t.get("quantity",""), t.get("pnl",""),
            t.get("result",""), t.get("balance_after",""),
        ])
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=paper_report_{today}.csv"},
    )


@app.get("/favicon.ico")
def favicon():
    return "", 204


# ══════════════════════════════════════════════════════════════════════════════
# Adaptive config yardımcıları
# ══════════════════════════════════════════════════════════════════════════════

ADAPTIVE_DEFAULTS: dict[str, Any] = {
    "enabled": False, "mode": "MONITOR", "auto_paper_enabled": False,
    "regime_min_confidence": 65, "final_decision_threshold": 78,
    "base_risk_pct": 0.25, "max_risk_pct": 0.50,
    "daily_loss_limit_pct": 1.0, "max_drawdown_pct": 5.0,
    "max_consecutive_losses": 3, "risk_reduction_after_losses": 2,
    "learning_enabled": True, "learning_interval_hours": 24,
    "minimum_learning_trades": 20, "max_daily_weight_change_pct": 5,
    "cooldown_minutes": 60, "break_even_enabled": False,
    "trailing_stop_enabled": False, "partial_take_profit_enabled": False,
    "kill_switch": False,
}


def _get_adaptive_cfg() -> dict[str, Any]:
    cfg, _ = load_config()
    base   = dict(ADAPTIVE_DEFAULTS)
    base.update((cfg or {}).get("adaptive_system", {}))
    return base


def _save_adaptive_cfg(adaptive: dict[str, Any]) -> tuple[bool, str]:
    with CONFIG_LOCK:
        cfg, err = load_config()
        if err or cfg is None:
            return False, err or "Ayar dosyası okunamadı."
        cfg["adaptive_system"] = adaptive
        try:
            atomic_write_json(CONFIG_PATH, cfg)
        except OSError as exc:
            return False, f"Kaydedilemedi: {exc}"
    return True, "Kaydedildi."


# ══════════════════════════════════════════════════════════════════════════════
# Mission 2200 — Operation Control Center (Agent 01)
#
# Tarayıcı → bu API → OperationControlService → Mission 2100
# ControlledExecutionAPI → izin kapısı → risk → kill-switch →
# yürütme servisi → defter. Tarayıcıdan borsa katmanına doğrudan
# yol YOKTUR; kapatma istekleri PAPER kontrollü kapatma NİYETİDİR.
# ══════════════════════════════════════════════════════════════════════════════

import operation_control_api as _oca                       # noqa: E402
from operation_control_errors import (                     # noqa: E402
    OperationControlValidationError as _OpValidationError)
from operation_control_models import (                     # noqa: E402
    AutomationCommand as _AutoCmd, SymbolCommand as _SymCmd)
from operation_control_service import (                    # noqa: E402
    CONFIRMATION_PHRASE as OPERATION_CONFIRMATION_PHRASE,
    OperationControlService)
from operation_control_snapshot import (                   # noqa: E402
    build_snapshot as _build_operation_snapshot)
from operation_control_store import (                       # noqa: E402
    OperationControlStateStore as _OpStateStore)

# Paylaşımlı durum dosyası: gunicorn worker'ları arasında otomasyon/
# idempotency/denetim/stop-new-entries durumunu tutarlı tutar.
OPERATION_STATE_PATH = ROOT / "alpha20_v1" / "operation_control_state.json"

_AUTOMATION_COMMANDS = {
    "start": _AutoCmd.START, "pause": _AutoCmd.PAUSE,
    "resume": _AutoCmd.RESUME, "stop": _AutoCmd.STOP,
}
_SYMBOL_COMMANDS = {
    "enable": _SymCmd.ENABLE, "pause": _SymCmd.PAUSE,
    "resume": _SymCmd.RESUME, "stop": _SymCmd.STOP,
}

_operation_lock = threading.Lock()
_operation_service: OperationControlService | None = None


def _operation_symbols(cfg: dict[str, Any] | None) -> tuple[str, ...]:
    symbols = (cfg or {}).get("symbols")
    if isinstance(symbols, list):
        cleaned = tuple(s.strip().upper() for s in symbols
                        if isinstance(s, str) and s.strip())
        if cleaned:
            return cleaned
    return ("BTCUSDT",)


def get_operation_service() -> OperationControlService:
    """Süreç başına tek Operasyon Kontrol Servisi (tembel kuruluş)."""
    global _operation_service
    with _operation_lock:
        if _operation_service is None:
            from controlled_execution_api import ControlledExecutionAPI
            from controlled_execution_foundation import (
                ControlledExecutionFoundation)
            from controlled_execution_policy import ExtensionRegistry
            from controlled_execution_router import (
                ControlledExecutionRouter)
            from execution_risk_models import (RiskDecision,
                                               RiskDecisionType)
            from micro_live_authorization import (
                MicroLiveAuthorizationService)
            from paper_broker import PaperBroker
            from paper_execution_service import (
                PaperExecutionService, StaticRiskEvaluator)
            from shadow_mode import ShadowModeService
            cfg, _ = load_config()
            foundation = ControlledExecutionFoundation(
                ExtensionRegistry())
            broker = PaperBroker(
                known_symbols=_operation_symbols(cfg))
            # Operatör kapatma niyetleri poz. azaltıcıdır; risk
            # kararı sertifikalı boru hattında yine de değerlendirilir.
            risk = StaticRiskEvaluator(RiskDecision(
                decision=RiskDecisionType.ALLOW))
            api = ControlledExecutionAPI(ControlledExecutionRouter(
                PaperExecutionService(broker=broker,
                                      foundation=foundation,
                                      risk_evaluator=risk),
                ShadowModeService(broker=broker,
                                  foundation=foundation,
                                  risk_evaluator=risk),
                MicroLiveAuthorizationService(
                    foundation=foundation)))
            # Paylaşımlı durum deposu: 2 gunicorn worker'ı aynı
            # otomasyon/idempotency/denetim durumunu görür; aynı
            # idempotency anahtarı hiçbir worker'da ikinci kez
            # kabul edilmez (flock + atomik JSON anlık görüntü).
            _operation_service = OperationControlService(
                api, clock=lambda: int(time.time()),
                state_store=_OpStateStore(OPERATION_STATE_PATH))
        return _operation_service


def _operation_kill_switch_active(cfg: dict[str, Any] | None) -> bool:
    """Kill-switch bayrağı adaptive_system altında tutulur
    (Mission 1500 /adaptive/kill-switch yolu ile aynı kaynak)."""
    adaptive = (cfg or {}).get("adaptive_system") or {}
    return bool(adaptive.get("kill_switch", False))


def _operation_raw() -> dict[str, Any]:
    """Operasyon anlık görüntüsü için ham veri topla.

    Erişilemeyen her bölüm UNKNOWN'a düşer — sahte sağlıklı
    durum üretilmez. Pozisyon/emir verisi mevcut salt-okunur
    görünümlerden gelir (borsa YAZMA çağrısı yoktur)."""
    from version import get_version
    cfg, cfg_err = load_config()
    ks_active = _operation_kill_switch_active(cfg)
    now = int(time.time())
    raw: dict[str, Any] = {
        "status": {
            "app_version": get_version(),
            "execution_mode": get_execution_mode(cfg),
            "kill_switch_state": "ACTIVE" if ks_active
            else "INACTIVE",
            # Saf modüller import edildiyse hazırdır; dış
            # bağımlılık gerektiren durumlar UNKNOWN kalır.
            "permission_gate_state": "READY",
            "risk_engine_state": "READY",
            "broker_state": "READY",
            "ledger_state": "READY",
            "reconciliation_state": "UNKNOWN",
            "last_sync_at": "UNKNOWN",
            "last_error_code":
                get_operation_service().last_error_code,
            "source_timestamp": None if cfg_err else now,
        },
        "positions": [], "orders": [], "products": [],
        "signals": [], "reconciliation": [],
        "risk_limits": {
            "max_open_positions":
                (cfg or {}).get("max_open_positions")
                if isinstance((cfg or {}).get(
                    "max_open_positions"), int) else None,
            "max_daily_loss": str((cfg or {}).get(
                "daily_loss_limit_pct"))
            if (cfg or {}).get("daily_loss_limit_pct")
            is not None else None,
            "allowed_markets": ["SPOT", "FUTURES"],
            "allowed_directions": ["LONG"],
            "allowed_execution_modes": list(EXECUTION_MODES),
            "micro_live_authorized": False,
            "authorization_expiry": "-",
            "kill_switch_active": ks_active,
        },
    }
    service = get_operation_service()
    # Spot-only: Futures pozisyon/emir sondası kaldırıldı.
    # positions ve orders listesi boş kalır (Futures yoktur).
    for symbol in _operation_symbols(cfg):
        raw["products"].append({
            "symbol": symbol,
            "market": "FUTURES",
            "strategy": "alpha20_v1",
            "signal_state": "UNKNOWN",
            "execution_mode": get_execution_mode(cfg),
            "direction": "UNKNOWN",
            "entry_eligible": service.symbol_state(
                symbol).value == "ENABLED" and
            service.automation_state.value == "RUNNING" and
            not service.stop_new_entries and not ks_active,
            "last_signal_at": "UNKNOWN",
            "last_decision": "UNKNOWN",
            "last_rejection_reason": "-",
        })
    return raw


def _operation_snapshot():
    service = get_operation_service()
    return _build_operation_snapshot(
        _operation_raw(), int(time.time()),
        service.automation_state, service.stop_new_entries,
        service.symbol_states())


def _operation_json(payload: dict[str, Any], status: int):
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store, private"
    return resp


def _operation_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _operation_actor() -> str:
    return session.get("username") or "operator"


def _operation_error(code: str, message: str, status: int | None = None):
    payload, http_status = _oca.error_envelope(
        code, message, g.get("request_id", "-"),
        int(time.time()), None, status)
    return _operation_json(payload, http_status)


@app.get("/home")
def trading_home_page():
    # Mission 2300: sahip odaklı varsayılan ana sayfa (yalnız
    # sunum; mevcut uçları okur, iş mantığı değişmedi).
    return _render_workspace("trading_home.html", "trading_home")


@app.get("/operation-center")
def operation_center_page():
    return _render_workspace("operation_control.html",
                             "operation_center")


# ── Mission 2300 A03: Ayarlar → Hesaplarım ─────────────────────────
# Hesap kayıt defteri sunum katmanıdır; işlem/otomasyon/risk
# mantığına dokunmaz. Bakiyeler mevcut dashboard_api ve PAPER
# defterinden okunur; bilinmeyen değer UNKNOWN kalır, asla tahmin
# edilmez.

@app.get("/settings/accounts")
def my_accounts_page():
    return _render_workspace("my_accounts.html", "my_accounts")


def _accounts_json(ok: bool, data=None, error_code=None, message="OK",
                   status=200):
    return jsonify({"ok": ok, "data": data, "error_code": error_code,
                    "message": message}), status


def _accounts_load():
    import accounts_registry as reg
    return reg, reg.load_registry()


def _automation_running() -> bool:
    try:
        cfg, _ = load_config()
        return bool((cfg or {}).get("bot_enabled"))
    except Exception:
        return False


@app.get("/api/accounts")
def api_accounts_list():
    import accounts_registry as reg
    try:
        accounts = reg.load_registry()
    except reg.RegistryError as exc:
        return _accounts_json(False, None, "REGISTRY_ERROR", str(exc),
                              500)
    cards = []
    for a in accounts:
        card = reg.card_view(a)
        # Kanonik snapshot durumu karta damgalanır: Hesaplarım rozeti
        # kayıt defteri bayrağından DEĞİL, Genel Bakış'la AYNI kanonik
        # snapshot'tan okunur (ekranlar arası tutarlılık sözleşmesi).
        if card["connected"] and card["connector_ready"]:
            card["connection_state"] = _account_snapshot(
                a["exchange"])["connection_state"]
        else:
            card["connection_state"] = "DISABLED"
        cards.append(card)
    return _accounts_json(True, {
        "accounts": cards,
        "execution_eligible": reg.execution_eligible(accounts),
        # Windows/yerel: API anahtarları Hesaplarım → Düzenle ile yerel
        # güvenli depoya kaydedilebilir; Replit'te Secrets kullanılır.
        "local_credentials_editable": not local_env.is_replit(),
        # Task 68: Eski Binance env isim uyarıları (yalnız metin; sır
        # değeri asla içermez) panelde banner olarak gösterilir.
        "legacy_env_warnings": local_env.legacy_name_warnings(),
    })


def _account_mutation(account_id: str, op):
    """Kayıt defteri mutasyonu için ortak sterile sarmalayıcı."""
    import accounts_registry as reg
    try:
        with reg.registry_lock():
            accounts = reg.load_registry()
            op(reg, accounts, account_id)
            reg.save_registry(accounts)
    except reg.RegistryError as exc:
        return _accounts_json(False, None, "VALIDATION", str(exc), 400)
    except OSError:
        # Depolama hatası: sterile yapılandırılmış yanıt (ham istisna
        # sızdırılmaz), 500.
        return _accounts_json(False, None, "STORAGE_ERROR",
                              "Kayıt defteri yazılamadı.", 500)
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   username=session.get("username", ""),
                   detail=f"my-accounts {op.__name__} {account_id}")
    return _accounts_json(True, {"account_id": account_id})


@app.post("/api/accounts/<account_id>/connect")
def api_accounts_connect(account_id: str):
    def _op(reg, accounts, aid):
        reg.connect(accounts, aid)
    _op.__name__ = "connect"
    return _account_mutation(account_id, _op)


@app.post("/api/accounts/<account_id>/disconnect")
def api_accounts_disconnect(account_id: str):
    running = _automation_running()

    def _op(reg, accounts, aid):
        reg.disconnect(accounts, aid, automation_running=running)
    _op.__name__ = "disconnect"
    return _account_mutation(account_id, _op)


@app.post("/api/accounts/<account_id>/primary")
def api_accounts_primary(account_id: str):
    def _op(reg, accounts, aid):
        reg.set_primary(accounts, aid)
    _op.__name__ = "set_primary"
    return _account_mutation(account_id, _op)


@app.post("/api/accounts/<account_id>/edit")
def api_accounts_edit(account_id: str):
    body = request.get_json(silent=True) or {}

    def _op(reg, accounts, aid):
        reg.edit(accounts, aid,
                 nickname=body.get("nickname"),
                 spot_enabled=body.get("spot_enabled"),
                 futures_enabled=body.get("futures_enabled"))
    _op.__name__ = "edit"
    return _account_mutation(account_id, _op)


@app.post("/api/accounts/<account_id>/credentials")
def api_accounts_credentials(account_id: str):
    """Windows/yerel: API anahtarlarını yerel güvenli depoya kaydeder.

    Replit'te 403 REPLIT_ENV — orada Secrets kanoniktir. Yanıt asla sır
    içermez (yalnız maskeli anahtar)."""
    import accounts_registry as reg
    import dashboard_api as dapi
    import exchange_credentials as xc
    if local_env.is_replit():
        return _accounts_json(
            False, None, "REPLIT_ENV",
            "Replit ortamında API anahtarları Secrets'ta yönetilir; "
            "yerel dosya deposu kullanılmaz.", 403)
    try:
        accounts = reg.load_registry()
        acc = reg.find(accounts, account_id)
    except reg.RegistryError as exc:
        return _accounts_json(False, None, "VALIDATION", str(exc), 400)
    if acc["exchange"] not in xc.EXCHANGES:
        return _accounts_json(False, None, "VALIDATION",
                              "Bu hesap türü için API anahtarı girişi "
                              "desteklenmiyor.", 400)
    body = request.get_json(silent=True) or {}
    try:
        xc.save_local(acc["exchange"], str(body.get("apiKey") or ""),
                      str(body.get("apiSecret") or ""))
    except ValueError as exc:
        return _accounts_json(False, None, "VALIDATION", str(exc), 400)
    except OSError:
        return _accounts_json(False, None, "STORAGE_ERROR",
                              "Anahtar deposu yazılamadı.", 500)
    # Yeni anahtarların hemen etkinleşmesi için önbellekler temizlenir
    # (restart gerekmez).
    dapi.invalidate_caches()
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   username=session.get("username", ""),
                   detail=f"my-accounts credentials {account_id}")
    return _accounts_json(True, {
        "account_id": account_id,
        "exchange": acc["exchange"],
        "api_key_masked": xc.masked_key(acc["exchange"]),
        "source": xc.source(acc["exchange"]),
    })


def _paper_balance() -> str:
    """PAPER simülasyon defteri bakiyesi (Decimal-str) veya UNKNOWN.

    ROOT'a bağlı STATE_PATH kullanılır — Windows'ta servis farklı çalışma
    dizininden başlatıldığında göreli yol yüzünden yanlış UNKNOWN
    üretilmez (gerçek Windows bug'ının kök nedeni)."""
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        bal = state.get("balance")
        if bal is None:
            return "UNKNOWN"
        return str(Decimal(str(bal)))
    except (OSError, ValueError, ArithmeticError):
        return "UNKNOWN"


def _connection_state(res: dict) -> str:
    """Kanonik durum türetimi TEK yerdedir: dashboard_api.connection_state.

    Bu sarmalayıcı yalnız delegasyondur — app katmanı kendi state
    mantığını TAŞIMAZ (ekranlar arası tutarlılık sözleşmesi)."""
    import dashboard_api as dapi
    return dapi.connection_state(res)


def _account_snapshot(exchange: str) -> dict:
    """Kanonik hesap snapshot'ı: cüzdanlar + USDT değeri + bağlantı
    durumu. TÜM ekranlar (Hesaplarım, Trading Home, Portföy, header)
    bu snapshot'ı OKUR — ikinci Binance çağrısı başlatmaz. Sır yok; ham
    istisna yok; bilinmeyen değer UNKNOWN. Tahmin YASAK."""
    import dashboard_api as dapi
    if exchange == "PAPER":
        bal = _paper_balance()
        return {"status": "OK" if bal != "UNKNOWN" else "UNKNOWN",
                "connection_state": ("HEALTHY" if bal != "UNKNOWN"
                                     else "CONNECTION_FAILED"),
                "value_usdt": bal, "last_sync_at": "-",
                "wallets": [{"name": "Simülasyon Defteri (USDT)",
                             "balance": bal, "available": bal,
                             "currency": "USDT"}]}
    if exchange == "BINANCE_GLOBAL":
        # Spot-only mimari: Global hesap SPOT bakiyeleriyle gösterilir.
        acc = dapi.global_spot_account()
        if not acc.get("ok"):
            state = _connection_state(acc)
            # Durum etiketi de KANONİK state'tir — tazelik etiketi
            # (UNAVAILABLE/UNKNOWN) ekranlara sızmaz (ROOT BUG).
            return {"status": state,
                    "connection_state": state,
                    "value_usdt": "UNKNOWN", "wallets": [],
                    "last_sync_at": (acc.get("meta") or {}).get(
                        "retrieved_at") or "UNKNOWN"}
        wallets = [{"name": f"Spot Cüzdanı ({h.get('asset')})",
                    "balance": h.get("amount"),
                    "available": h.get("amount"),
                    "currency": h.get("asset")}
                   for h in (acc.get("top_holdings") or [])]
        if not wallets:
            wallets = [{"name": "Spot Cüzdanı (USDT)",
                        "balance": acc.get("usdt_free") or "0",
                        "available": acc.get("usdt_free") or "0",
                        "currency": "USDT"}]
        total = acc.get("total_spot_value_usdt") or "UNKNOWN"
        if acc.get("valuation") == "PARTIAL" and total != "UNKNOWN":
            total = f"{total} (kısmi)"
        return {"status": "OK",
                "connection_state": _connection_state(acc),
                "value_usdt": total,
                "last_sync_at": (acc.get("meta") or {}).get(
                    "retrieved_at") or "UNKNOWN",
                "wallets": wallets}
    if exchange == "BINANCE_TR":
        acc = dapi.tr_account()
        if not acc.get("ok"):
            state = _connection_state(acc)
            # Durum etiketi de KANONİK state'tir — tazelik etiketi
            # (UNAVAILABLE/UNKNOWN) ekranlara sızmaz (ROOT BUG).
            return {"status": state,
                    "connection_state": state,
                    "value_usdt": "UNKNOWN", "wallets": [],
                    "last_sync_at": (acc.get("meta") or {}).get(
                        "retrieved_at") or "UNKNOWN"}
        try:
            usdt_total = str(Decimal(acc.get("usdt_free", "0")) +
                             Decimal(acc.get("usdt_locked", "0")))
            try_total = str(Decimal(acc.get("try_free", "0")) +
                            Decimal(acc.get("try_locked", "0")))
        except ArithmeticError:
            usdt_total, try_total = "UNKNOWN", "UNKNOWN"
        # TRY→USDT dönüşümü tahmin gerektirir; tahmin YASAK. TRY
        # bakiyesi sıfır değilse toplam USDT değeri UNKNOWN kalır.
        value = (usdt_total if try_total not in ("UNKNOWN",) and
                 Decimal(try_total) == 0 and usdt_total != "UNKNOWN"
                 else "UNKNOWN")
        return {"status": "OK",
                "connection_state": _connection_state(acc),
                "value_usdt": value,
                "last_sync_at": (acc.get("meta") or {}).get(
                    "retrieved_at") or "UNKNOWN",
                "wallets": [
                    {"name": "Spot Cüzdanı (USDT)",
                     "balance": usdt_total,
                     "available": acc.get("usdt_free", "UNKNOWN"),
                     "currency": "USDT"},
                    {"name": "Spot Cüzdanı (TRY)",
                     "balance": try_total,
                     "available": acc.get("try_free", "UNKNOWN"),
                     "currency": "TRY"}]}
    return {"status": "UNKNOWN", "connection_state": "DISABLED",
            "value_usdt": "UNKNOWN",
            "wallets": [], "last_sync_at": "UNKNOWN"}


@app.get("/api/accounts/wallets")
def api_accounts_wallets():
    """Trading Home cüzdan panelinin TEK veri kaynağı: yalnız BAĞLI
    kişisel hesaplar. Borsa sayfalarına doğrudan bağımlılık yok."""
    import accounts_registry as reg
    try:
        accounts = reg.load_registry()
    except reg.RegistryError as exc:
        return _accounts_json(False, None, "REGISTRY_ERROR", str(exc),
                              500)
    out = []
    for acc in accounts:
        if not acc["connected"]:
            continue
        # KANONİK snapshot'a delegasyon: bu endpoint kendi exchange
        # health/balance hesaplamasını YAPMAZ (tek doğruluk kaynağı).
        snap = _account_snapshot(acc["exchange"])
        card = reg.card_view(acc)
        state = snap["connection_state"]
        out.append({
            "account_id": acc["account_id"],
            "nickname": acc["nickname"],
            "display_name": card["display_name"],
            "logo": card["logo"],
            "primary": acc["primary"],
            "connection_state": state,
            "status": "OK" if state in ("HEALTHY", "STALE") else state,
            "value_usdt": snap["value_usdt"],
            "wallet_count": len(snap["wallets"]),
            "wallets": snap["wallets"],
            "last_sync_at": snap.get("last_sync_at", "UNKNOWN"),
        })
    return _accounts_json(True, {"accounts": out})


@app.get("/api/accounts/portfolio")
def api_accounts_portfolio():
    """Toplam portföy = bağlı hesapların toplamı. Bilinmeyen bileşen
    varsa toplam UNKNOWN kalır — asla tahmin edilmez."""
    import accounts_registry as reg
    try:
        accounts = reg.load_registry()
    except reg.RegistryError as exc:
        return _accounts_json(False, None, "REGISTRY_ERROR", str(exc),
                              500)
    components, total, unknown = [], Decimal("0"), False
    for acc in accounts:
        if not acc["connected"]:
            continue
        # Aynı KANONİK snapshot — bağımsız exchange fetch YOK; bir
        # hesabın NOT_CONFIGURED olması diğerlerinin (ör. PAPER)
        # değerini etkilemez, yalnız toplamı UNKNOWN bırakır.
        snap = _account_snapshot(acc["exchange"])
        value = snap["value_usdt"]
        components.append({"account_id": acc["account_id"],
                           "nickname": acc["nickname"],
                           "connection_state": snap["connection_state"],
                           "value_usdt": value})
        if value == "UNKNOWN":
            unknown = True
        else:
            try:
                total += Decimal(value)
            except ArithmeticError:
                unknown = True
    return _accounts_json(True, {
        "components": components,
        "total_usdt": "UNKNOWN" if unknown else str(total),
        "note": ("Bir veya daha fazla hesap bakiyesi bilinmiyor; "
                 "toplam tahmin edilmez." if unknown else "OK"),
    })


@app.post("/api/accounts/<account_id>/test")
def api_accounts_test(account_id: str):
    """Basit bağlantı testi. Yalnız sade durumlar; ham istisna yok."""
    import accounts_registry as reg
    import dashboard_api as dapi
    try:
        accounts = reg.load_registry()
        acc = reg.find(accounts, account_id)
    except reg.RegistryError as exc:
        return _accounts_json(False, None, "VALIDATION", str(exc), 400)
    conn = reg.CONNECTORS[acc["exchange"]]
    checks = {"connected": "UNKNOWN", "authentication": "UNKNOWN",
              "wallet_access": "UNKNOWN", "spot_permission": "UNKNOWN",
              "trading_permission": "UNKNOWN",
              "synchronization": "UNKNOWN"}
    if not conn.supported:
        return _accounts_json(True, {"account_id": account_id,
                                     "overall": "NOT_READY",
                                     "checks": checks})
    if acc["exchange"] == "PAPER":
        bal = _paper_balance()
        ok = bal != "UNKNOWN"
        for key in checks:
            checks[key] = "OK" if ok else "UNKNOWN"
        checks["spot_permission"] = "NOT_SUPPORTED"
        return _accounts_json(True, {
            "account_id": account_id,
            "overall": "HEALTHY" if ok else "UNKNOWN",
            "checks": checks})
    if acc["exchange"] == "BINANCE_GLOBAL":
        # Spot-only mimari: sağlık testi SPOT hesabıyla yapılır.
        res = dapi.global_spot_account()
        if res.get("ok"):
            checks.update(connected="OK", authentication="OK",
                          wallet_access="OK",
                          spot_permission="OK",
                          trading_permission=(
                              "OK" if res.get("can_trade_flag")
                              else "READ_ONLY"),
                          synchronization="OK")
    else:  # BINANCE_TR
        res = dapi.tr_account()
        if res.get("ok"):
            checks.update(connected="OK", authentication="OK",
                          wallet_access="OK", spot_permission="OK",
                          synchronization="OK")
    # Kanonik durum: credential yoksa NOT_CONFIGURED (yanıltıcı FAILED
    # teşhisi üretilmez); diğer ekranlar aynı snapshot'ı okur.
    state = _connection_state(res)
    if res.get("ok"):
        overall = "HEALTHY"
    elif state == "NOT_CONFIGURED":
        overall = "NOT_CONFIGURED"
        checks["connected"] = "NOT_CONFIGURED"
    else:
        overall = "FAILED"
    return _accounts_json(True, {"account_id": account_id,
                                 "overall": overall,
                                 "connection_state": state,
                                 "checks": checks})


@app.post("/api/accounts/<account_id>/sync")
def api_accounts_sync(account_id: str):
    """Elle eşitleme: bakiyeleri tazeler ve eşitleme zamanını yazar.
    Normal kullanımda otomatik yoklama yeterlidir."""
    import accounts_registry as reg
    try:
        with reg.registry_lock():
            accounts = reg.load_registry()
            acc = reg.find(accounts, account_id)
            if not acc["connected"]:
                raise reg.RegistryError(
                    "Bağlı olmayan hesap eşitlenemez.")
            snap = _account_snapshot(acc["exchange"])
            reg.touch_sync(accounts, account_id)
            reg.save_registry(accounts)
    except reg.RegistryError as exc:
        return _accounts_json(False, None, "VALIDATION", str(exc), 400)
    except OSError:
        return _accounts_json(False, None, "STORAGE_ERROR",
                              "Kayıt defteri yazılamadı.", 500)
    return _accounts_json(True, {"account_id": account_id,
                                 "status": snap["status"],
                                 "wallet_count": len(snap["wallets"]),
                                 "value_usdt": snap["value_usdt"]})


@app.get("/api/operation-control/status")
def api_operation_status():
    snapshot = _operation_snapshot()
    data = _oca.serialize_view(snapshot.status)
    data["confirmation_phrase"] = OPERATION_CONFIRMATION_PHRASE
    # Task 70: Eski Binance env isim uyarıları ana panelde banner olarak
    # gösterilir (yalnız metin; sır değeri asla içermez).
    data["legacy_env_warnings"] = local_env.legacy_name_warnings()
    payload, status = _oca.read_envelope(
        data, snapshot, g.get("request_id", "-"),
        snapshot.generated_at)
    return _operation_json(payload, status)


def _operation_read(section: str):
    snapshot = _operation_snapshot()
    rows = [_oca.serialize_view(v)
            for v in getattr(snapshot, section)]
    payload, status = _oca.read_envelope(
        {section: rows, "count": len(rows)}, snapshot,
        g.get("request_id", "-"), snapshot.generated_at)
    return _operation_json(payload, status)


@app.get("/api/operation-control/products")
def api_operation_products():
    return _operation_read("products")


@app.get("/api/operation-control/positions")
def api_operation_positions():
    return _operation_read("positions")


@app.get("/api/operation-control/orders")
def api_operation_orders():
    return _operation_read("orders")


@app.get("/api/operation-control/signals")
def api_operation_signals():
    return _operation_read("signals")


@app.get("/api/operation-control/reconciliation")
def api_operation_reconciliation():
    return _operation_read("reconciliation")


@app.get("/api/operation-control/risk")
def api_operation_risk():
    snapshot = _operation_snapshot()
    data = (_oca.serialize_view(snapshot.risk_limits)
            if snapshot.risk_limits is not None else None)
    payload, status = _oca.read_envelope(
        {"risk_limits": data}, snapshot,
        g.get("request_id", "-"), snapshot.generated_at)
    return _operation_json(payload, status)


@app.get("/api/operation-control/audit")
def api_operation_audit():
    snapshot = _operation_snapshot()
    service = get_operation_service()
    payload, status = _oca.read_envelope(
        {"audit": _oca.serialize_audit(service.audit.tail(200)),
         "count": len(service.audit)}, snapshot,
        g.get("request_id", "-"), snapshot.generated_at)
    return _operation_json(payload, status)


_TRADING_ENABLING_COMMANDS = {"start", "resume", "enable"}


def _operation_kill_switch_block(command: str):
    """Kill-switch etkinken ticareti AÇAN komutlar reddedilir
    (fail-closed; durum makinesi süreç-yerel, bayrak globaldir)."""
    if command not in _TRADING_ENABLING_COMMANDS:
        return None
    cfg, _ = load_config()
    if not _operation_kill_switch_active(cfg):
        return None
    return _operation_error(
        "KILL_SWITCH_ACTIVE",
        "Kill-switch etkin — ticareti açan komut reddedildi.", 423)


@app.post("/api/operation-control/automation/<command>")
def api_operation_automation(command: str):
    cmd = _AUTOMATION_COMMANDS.get(command)
    if cmd is None:
        return _operation_error("UNKNOWN_TARGET:command",
                                "Bilinmeyen otomasyon komutu.")
    blocked = _operation_kill_switch_block(command)
    if blocked is not None:
        return blocked
    body = _operation_body()
    try:
        result = get_operation_service().execute_automation_command(
            cmd, _operation_actor(),
            body.get("idempotency_key"))
    except _OpValidationError as exc:
        return _operation_error("MALFORMED_REQUEST", str(exc))
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   username=session.get("username", ""),
                   detail=f"operation automation {command}: "
                          f"{result.status.value}")
    payload, status = _oca.action_envelope(
        result, _operation_snapshot(), int(time.time()))
    return _operation_json(payload, status)


@app.post("/api/operation-control/symbols/<symbol>/<command>")
def api_operation_symbol(symbol: str, command: str):
    cmd = _SYMBOL_COMMANDS.get(command)
    if cmd is None:
        return _operation_error("UNKNOWN_TARGET:command",
                                "Bilinmeyen sembol komutu.")
    blocked = _operation_kill_switch_block(command)
    if blocked is not None:
        return blocked
    body = _operation_body()
    try:
        result = get_operation_service().execute_symbol_command(
            symbol, cmd, _operation_actor(),
            body.get("idempotency_key"))
    except _OpValidationError as exc:
        return _operation_error("MALFORMED_REQUEST", str(exc))
    payload, status = _oca.action_envelope(
        result, _operation_snapshot(), int(time.time()))
    return _operation_json(payload, status)


def _operation_close_context(cfg: dict[str, Any] | None,
                             positions):
    """Kapatma niyeti için sertifikalı model bağlamı kur.

    PAPER kontrollü kapatma NİYETİ: görünen pozisyondan türetilen
    defter anlık görüntüsü ile sertifikalı boru hattına girilir.
    Kill-switch etkinse anlık görüntü yazmayı REDDEDER."""
    from decimal import Decimal as _D

    from controlled_execution_models import (
        ControlledExecutionMode, ControlledExecutionPolicy)
    from execution_kill_switch_models import (
        KillSwitchReason, KillSwitchSnapshot, KillSwitchState)
    from paper_models import PaperLedgerSnapshot, PaperPosition
    ks_active = _operation_kill_switch_active(cfg)
    now = int(time.time())
    kill_switch = KillSwitchSnapshot(
        # Sertifikalı katmanda ENABLED = koruma sağlıklı ve yazma
        # izinli; acil durdurma etkinse DISABLED ile RED edilir.
        state=KillSwitchState.DISABLED if ks_active
        else KillSwitchState.ENABLED,
        reason=KillSwitchReason.MANUAL,
        timestamp=now, sequence_id=now)
    policy = ControlledExecutionPolicy(
        mode=ControlledExecutionMode.PAPER,
        simulated_fill_allowed=True)
    paper_positions = []
    budget = _D("0")
    for view in positions:
        if view.side.upper() in ("BUY", "LONG") and \
                view.quantity is not None and \
                view.entry_price is not None and \
                view.quantity > 0 and view.entry_price > 0:
            paper_positions.append(PaperPosition(
                symbol=view.symbol, quantity=view.quantity,
                cost_basis=view.entry_price * view.quantity))
        if view.quantity is not None and \
                view.current_price is not None and \
                view.quantity > 0 and view.current_price > 0:
            budget += view.quantity * view.current_price * 2
    cost_total = sum((p.cost_basis for p in paper_positions),
                     _D("0"))
    ledger = PaperLedgerSnapshot(
        quote_asset="USDT",
        initial_cash=cost_total + budget,
        cash=budget, reserved_cash=_D("0"),
        realized_pnl=_D("0"), commission_paid=_D("0"),
        positions=tuple(paper_positions))
    return policy, kill_switch, ledger


@app.post("/api/operation-control/positions/<position_id>/close")
def api_operation_position_close(position_id: str):
    body = _operation_body()
    snapshot = _operation_snapshot()
    target = next((p for p in snapshot.positions
                   if p.position_id == position_id.upper()),
                  None)
    if target is None:
        return _operation_error("UNKNOWN_TARGET:position",
                                "Pozisyon bulunamadı.")
    cfg, _ = load_config()
    policy, kill_switch, ledger = _operation_close_context(
        cfg, snapshot.positions)
    try:
        result = get_operation_service().request_position_close(
            target, ledger, policy, kill_switch,
            _operation_actor(), body.get("reason") or "",
            body.get("confirm_phrase") or "",
            body.get("idempotency_key") or "")
    except _OpValidationError as exc:
        return _operation_error("MALFORMED_REQUEST", str(exc))
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   username=session.get("username", ""),
                   detail=f"operation close {position_id}: "
                          f"{result.status.value}")
    payload, status = _oca.action_envelope(
        result, snapshot, int(time.time()),
        "PAPER kontrollü kapatma niyeti")
    return _operation_json(payload, status)


@app.post("/api/operation-control/global/stop-new-entries")
def api_operation_stop_new_entries():
    body = _operation_body()
    try:
        result = get_operation_service().stop_new_entries_action(
            _operation_actor(), body.get("reason") or "",
            body.get("confirm_phrase") or "",
            body.get("idempotency_key") or "")
    except _OpValidationError as exc:
        return _operation_error("MALFORMED_REQUEST", str(exc))
    payload, status = _oca.action_envelope(
        result, _operation_snapshot(), int(time.time()))
    return _operation_json(payload, status)


@app.post("/api/operation-control/global/request-close-all")
def api_operation_request_close_all():
    body = _operation_body()
    snapshot = _operation_snapshot()
    cfg, _ = load_config()
    policy, kill_switch, ledger = _operation_close_context(
        cfg, snapshot.positions)
    try:
        result = get_operation_service().request_close_all(
            snapshot.positions, ledger, policy, kill_switch,
            _operation_actor(), body.get("reason") or "",
            body.get("confirm_phrase") or "",
            body.get("idempotency_key") or "")
    except _OpValidationError as exc:
        return _operation_error("MALFORMED_REQUEST", str(exc))
    slog.log_event(slog.STARTUP, ip=auth.get_client_ip(),
                   username=session.get("username", ""),
                   detail=f"operation close-all: "
                          f"{result.status.value}")
    payload, status = _oca.action_envelope(
        result, snapshot, int(time.time()),
        "Pozisyon başına ayrı PAPER kapatma niyeti")
    return _operation_json(payload, status)


@app.post("/api/operation-control/global/kill-switch")
def api_operation_kill_switch():
    body = _operation_body()
    engage = body.get("engage")
    if not isinstance(engage, bool):
        return _operation_error("MALFORMED_REQUEST:engage",
                                "engage boolean olmalıdır.")
    try:
        result = get_operation_service().record_kill_switch(
            _operation_actor(), engage,
            body.get("reason") or "",
            body.get("confirm_phrase") or "",
            body.get("idempotency_key") or "")
    except _OpValidationError as exc:
        return _operation_error("MALFORMED_REQUEST", str(exc))
    if result.status.value == "COMPLETED":
        # Sertifikalı kill-switch mekanizması (Mission 1500 yolu).
        cfg = _get_adaptive_cfg()
        if engage:
            sg.activate_kill_switch(
                "Operation Center acil durdurma.")
            cfg["kill_switch"] = True
        else:
            sg.deactivate_kill_switch()
            cfg["kill_switch"] = False
        _save_adaptive_cfg(cfg)
        slog.log_event(slog.KILL_SWITCH,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip(),
                       detail="operation-center "
                              f"{'activated' if engage else 'deactivated'}")
    payload, status = _oca.action_envelope(
        result, _operation_snapshot(), int(time.time()),
        "Ticaret bloklandı; pozisyonlar KAPATILMADI — ayrı "
        "kapatma niyeti gerekir." if engage else None)
    return _operation_json(payload, status)


# ══════════════════════════════════════════════════════════════════════════════
# Mission 2200 Agent 02 — İşlem Çalışma Alanı (workspace) uçları
# Salt-okunur GET uçları: portföy çubuğu, performans, broker
# sağlığı, strateji paneli, günlük ve CSV dışa aktarım.
# Doğrudan borsa YAZMA çağrısı yoktur.
# ══════════════════════════════════════════════════════════════════════════════

import operation_workspace_api as _owa
import operation_workspace_service as _ows


def _workspace_trades_raw() -> list[Any]:
    """Kapalı işlem kayıtları — tek kaynak trade_history.json.
    Dosya yoksa boş liste (UI dürüstçe UNKNOWN gösterir)."""
    path = Path("alpha20_v1/trade_history.json")
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            # Bozuk satır (string/null vb.) sessizce atlanmaz —
            # olduğu gibi iletilir; parse_trades onu Mapping
            # olmadığı için düşürür ve dropped_records'a sayar.
            out.append(row)
            continue
        closed = row.get("closed_at")
        closed_epoch = None
        if isinstance(closed, str):
            try:
                closed_epoch = int(datetime.fromisoformat(
                    closed.replace("Z", "+00:00")).timestamp())
            except ValueError:
                closed_epoch = None
        opened = row.get("opened_at") or row.get("time")
        opened_epoch = None
        if isinstance(opened, str):
            try:
                opened_epoch = int(datetime.fromisoformat(
                    opened.replace("Z", "+00:00")).timestamp())
            except ValueError:
                opened_epoch = None
        elif isinstance(opened, (int,)) and not isinstance(
                opened, bool):
            opened_epoch = opened
        out.append({
            "realized_pnl": str(row.get("pnl"))
            if row.get("pnl") is not None else None,
            "fees": str(row.get("fee_usdt"))
            if row.get("fee_usdt") is not None else None,
            "closed_at": closed_epoch,
            "opened_at": opened_epoch,
            "symbol": row.get("symbol"),
        })
    return out


def _workspace_equity_raw() -> list[dict[str, Any]]:
    path = Path("alpha20_v1/equity_curve.json")
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = row.get("timestamp")
        at = None
        if isinstance(ts, str):
            try:
                at = int(datetime.fromisoformat(
                    ts.replace("Z", "+00:00")).timestamp())
            except ValueError:
                at = None
        elif isinstance(ts, (int,)) and not isinstance(ts, bool):
            at = ts
        equity = row.get("equity")
        out.append({"at": at, "equity": str(equity)
                    if equity is not None else None})
    return out


def _workspace_account_raw() -> dict[str, Any]:
    """Bot muhasebe durumundan (state.json) bakiye — PAPER
    defteridir; yoksa alanlar UNKNOWN kalır. ROOT'a bağlı STATE_PATH
    kullanılır (Windows çalışma dizini bağımsızlığı)."""
    try:
        state = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    balance = state.get("balance")
    if balance is None:
        return {}
    text = str(balance)
    return {"portfolio_value": text, "cash": text, "equity": text}


# Broker sağlık sondası — _operation_raw'daki başarılı okuma
# etrafında ölçülür; süreç-yereldir (bilinen sınır #6 ile aynı).
_workspace_probe: dict[str, Any] = {
    "heartbeat_at": None, "latency_ms": None,
    "reconnect_count": None, "api_status": "UNKNOWN",
    "rate_limit_state": "UNKNOWN",
    "synchronization_state": "UNKNOWN",
    "authentication_state": "UNKNOWN",
    "permission_state": "READ_ONLY",
}


@app.get("/api/operation-control/workspace/portfolio")
def api_workspace_portfolio():
    snapshot = _operation_snapshot()
    view = _ows.build_portfolio_view(
        snapshot.positions, _workspace_account_raw(),
        _workspace_trades_raw(), int(time.time()),
        freshness=snapshot.status.data_freshness.value)
    payload = _owa.workspace_envelope(
        {"portfolio": _oca.serialize_view(view)},
        snapshot, g.get("request_id", "-"), int(time.time()))
    return _operation_json(payload, 200)


@app.get("/api/operation-control/workspace/performance")
def api_workspace_performance():
    snapshot = _operation_snapshot()
    view = _ows.build_performance_view(
        _workspace_trades_raw(), _workspace_equity_raw(),
        int(time.time()))
    payload = _owa.workspace_envelope(
        {"performance": _oca.serialize_view(view)},
        snapshot, g.get("request_id", "-"), int(time.time()))
    return _operation_json(payload, 200)


@app.get("/api/operation-control/workspace/broker-health")
def api_workspace_broker_health():
    snapshot = _operation_snapshot()  # sondayı da tazeler
    view = _ows.build_broker_health_view(
        dict(_workspace_probe), int(time.time()))
    payload = _owa.workspace_envelope(
        {"broker_health": _oca.serialize_view(view)},
        snapshot, g.get("request_id", "-"), int(time.time()))
    return _operation_json(payload, 200)


@app.get("/api/operation-control/workspace/strategies")
def api_workspace_strategies():
    snapshot = _operation_snapshot()
    rows = _ows.build_strategy_rows(
        snapshot.products, snapshot.positions, snapshot.signals)
    payload = _owa.workspace_envelope(
        {"strategies": _owa.serialize_rows(rows)},
        snapshot, g.get("request_id", "-"), int(time.time()))
    return _operation_json(payload, 200)


@app.get("/api/operation-control/workspace/journal")
def api_workspace_journal():
    snapshot = _operation_snapshot()
    service = get_operation_service()
    events = _ows.build_journal_events(
        snapshot.signals, service.audit.records())
    payload = _owa.workspace_envelope(
        {"journal": _owa.serialize_rows(events)},
        snapshot, g.get("request_id", "-"), int(time.time()))
    return _operation_json(payload, 200)


@app.get("/api/operation-control/workspace/orders/"
         "<order_id>/lifecycle")
def api_workspace_order_lifecycle(order_id: str):
    """Tek emrin salt-okunur yaşam döngüsü zinciri (Task 29).
    Yalnız gözlemlenen gerçek veri; emir bulunamazsa dürüst 404."""
    snapshot = _operation_snapshot()
    order = next((o for o in snapshot.orders
                  if o.order_id == order_id), None)
    if order is None:
        return _operation_error(
            "ORDER_NOT_FOUND",
            "Emir anlık görüntüde bulunamadı.", 404)
    events = _ows.build_order_lifecycle_events(
        order, snapshot.signals,
        get_operation_service().audit.records())
    payload = _owa.workspace_envelope(
        {"order_id": order.order_id,
         "lifecycle": _owa.serialize_rows(events),
         "count": len(events)},
        snapshot, g.get("request_id", "-"), int(time.time()))
    return _operation_json(payload, 200)


@app.get("/api/operation-control/workspace/export/<name>.csv")
def api_workspace_export_csv(name: str):
    if name not in _owa.CSV_EXPORTS:
        return _operation_error("MALFORMED_REQUEST:export", name
                                if name in _owa.CSV_EXPORTS
                                else "unknown export", 404)
    snapshot = _operation_snapshot()
    if name == "positions":
        rows: tuple = snapshot.positions
    elif name == "orders":
        rows = snapshot.orders
    elif name == "signals":
        rows = snapshot.signals
    else:
        rows = _ows.build_journal_events(
            snapshot.signals,
            get_operation_service().audit.records())
    body = _owa.rows_to_csv(rows)
    resp = app.response_class(body, mimetype="text/csv")
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="operation_{name}.csv"')
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# Başlangıç
# ══════════════════════════════════════════════════════════════════════════════

def _get_main_config() -> dict[str, Any]:
    cfg, _ = load_config()
    return cfg or DEFAULT_CONFIG


if __name__ == "__main__":
    # Başlangıç güvenlik kontrolleri
    validate_startup_config()
    enforce_paper_mode_lock()
    # Akıllı seçim otomatik döngüsü
    um.start_auto_loop(_get_main_config)
    # Uyarlanabilir motor — yalnızca config'de enabled=true ise
    cfg0 = _get_main_config()
    if cfg0.get("adaptive_system", {}).get("enabled", False):
        ac.start_controller_loop()
    port    = int(os.environ.get("PORT", "5000"))
    _debug  = os.environ.get("FLASK_ENV", "").lower() == "development"
    app.run(host="0.0.0.0", port=port, debug=_debug)
