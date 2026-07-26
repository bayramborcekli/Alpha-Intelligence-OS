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

from flask import Flask, Response, render_template, request, redirect, session, url_for
from flask_wtf.csrf import CSRFProtect, CSRFError

# ── alpha20_v1/ modülleri sys.path üzerinden import ──────────────────────────
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "alpha20_v1"))

import universe_manager as um    # noqa: E402
import metrics_store    as ms    # noqa: E402
import safety_guard     as sg    # noqa: E402
import auto_controller  as ac    # noqa: E402
import learning_engine  as le    # noqa: E402
import auth                      # noqa: E402
import security_log     as slog  # noqa: E402

app = Flask(__name__)

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
def _security_gate():
    """Her istekte kimlik doğrulama kontrolü. TESTING=True ise atlanır."""
    if app.config.get("TESTING"):
        app.config["WTF_CSRF_ENABLED"] = False
        return
    exempt = {"/login", "/logout", "/setup", "/setup/hash", "/setup/check", "/favicon.ico", "/health"}
    if request.path in exempt or request.path.startswith("/static/"):
        return
    # Parola yapılandırılmamışsa sihirbaza yönlendir
    if not auth.password_hash_configured():
        return redirect(url_for("setup_wizard"))
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))
    if auth._session_expired():
        session.clear()
        return redirect(url_for("login"))


@app.after_request
def _security_headers(response: Response) -> Response:
    """Tüm yanıtlara güvenlik HTTP başlıkları ekle."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]     = "geolocation=(), camera=(), microphone=()"
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp
    if os.environ.get("FLASK_ENV") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.errorhandler(CSRFError)
def _csrf_error(exc: CSRFError):  # type: ignore[misc]
    ip = auth.get_client_ip()
    slog.log_event(slog.CSRF_FAIL, ip=ip, detail=str(exc)[:80])
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
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            pid = int(entry.name)
            if pid != os.getpid() and is_bot_command(pid):
                pids.append(pid)
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
    return bool(find_bot_pids())


def start_bot() -> tuple[bool, str]:
    with CONFIG_LOCK:
        if find_bot_pids():
            return False, "Bot zaten çalışıyor."
        if not BOT_PATH.exists():
            return False, "Bot dosyası bulunamadı."
        try:
            out  = BOT_OUTPUT.open("a", encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, str(BOT_PATH)],
                cwd=str(ROOT), stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
            write_pid(proc.pid)
            out.close()
        except (OSError, ValueError) as exc:
            return False, f"Bot başlatılamadı ({type(exc).__name__})."
    return True, "Bot başlatıldı."


def stop_bot() -> tuple[bool, str]:
    with CONFIG_LOCK:
        pid = read_pid()
        if pid is None or not is_bot_command(pid):
            PID_PATH.unlink(missing_ok=True)
            return False, "Uygulamanın başlattığı çalışan bot bulunamadı."
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                if not Path(f"/proc/{pid}").exists():
                    break
                time.sleep(0.1)
            if Path(f"/proc/{pid}").exists():
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
        return redirect(url_for("login"))
    return render_template("setup.html")


@app.post("/setup/hash")
def setup_generate_hash():
    """
    Girilen paroladan Werkzeug PBKDF2 hash üret ve döndür.
    Parola sunucuda saklanmaz; yalnızca hash döndürülür.
    Yalnızca ADMIN_PASSWORD_HASH yapılandırılmamışken erişilebilir.
    """
    if auth.password_hash_configured():
        return {"error": "Kurulum tamamlanmış. Bu endpoint artık devre dışı."}, 403
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    if not password or not isinstance(password, str):
        return {"error": "Parola boş olamaz."}, 400
    if len(password) < 6:
        return {"error": "Parola en az 6 karakter olmalıdır."}, 400
    if len(password) > 1024:
        return {"error": "Parola çok uzun."}, 400
    from werkzeug.security import generate_password_hash
    pw_hash = generate_password_hash(password)
    ip = auth.get_client_ip()
    slog.log_event(slog.STARTUP, detail="setup: hash generated", ip=ip)
    return {"hash": pw_hash}


@app.get("/setup/check")
def setup_check():
    """Parola yapılandırılmış mı diye kontrol et (sihirbaz doğrulama adımı)."""
    return {"configured": auth.password_hash_configured()}


@app.route("/login", methods=["GET", "POST"])
def login():
    # Parola yapılandırılmamışsa sihirbaza yönlendir
    if not auth.password_hash_configured():
        return redirect(url_for("setup_wizard"))
    if session.get("logged_in"):
        return redirect(url_for("index"))

    error: str | None = None
    not_configured    = not auth.password_hash_configured()
    # next_url — yalnızca aynı origin göreceli yollar
    next_url = request.args.get("next") or request.form.get("next") or "/"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

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
        setting_fields=setting_fields(config),
        config_error=config_error, state_error=state_error,
        message=message, message_type=message_type,
        smart=smart, perf=perf,
        adaptive=adaptive_ctx,
        app_version=get_version(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Rotalar — temel panel
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def index():
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
    ok, msg = start_bot()
    if ok:
        slog.log_event(slog.BOT_START,
                       username=session.get("username", ""),
                       ip=auth.get_client_ip())
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/bot/stop")
def bot_stop():
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


@app.get("/api/daily-report")
def api_daily_report():
    cfg, _ = load_config()
    return _build_daily_report(cfg)


@app.get("/api/daily-report/export")
def export_daily_report():
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
