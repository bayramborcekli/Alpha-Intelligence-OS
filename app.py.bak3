"""
app.py — Alpha-20 v1 PAPER Bot Kontrol Paneli
Flask web arayüzü: bot yönetimi, ayarlar, coin listesi ve Akıllı Coin Seçimi.
API anahtarı, canlı emir veya gerçek para işlemi içermez.
"""
from __future__ import annotations

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

from flask import Flask, render_template, request

# universe_manager alpha20_v1/ altında; sys.path ile import
sys.path.insert(0, str(Path(__file__).resolve().parent / "alpha20_v1"))
import universe_manager as um  # noqa: E402

app = Flask(__name__)
ROOT        = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "alpha20_v1" / "config.json"
STATE_PATH  = ROOT / "alpha20_v1" / "state.json"
LOG_PATH    = ROOT / "alpha20_v1" / "alpha20.log"
BOT_PATH    = ROOT / "alpha20_v1" / "alpha20.py"
PID_PATH    = ROOT / "alpha20_v1" / ".bot.pid"
BOT_OUTPUT  = ROOT / "alpha20_v1" / "bot_process.log"

CONFIG_LOCK          = threading.RLock()
INTEGER_PATTERN      = re.compile(r"^[0-9]+$")
SYMBOL_PATTERN       = re.compile(r"^[A-Z0-9]+USDT$")
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
    "minimum_score":        ("int",   0,   100),
    "scan_seconds":         ("int",   15,  3600),
    "risk_per_trade_pct":   ("float", 0.1, 2.0),
    "daily_loss_limit_pct": ("float", 0.5, 10.0),
    "max_consecutive_losses": ("int", 1,   10),
    "reward_risk_ratio":    ("float", 1.0, 5.0),
    "atr_stop_multiplier":  ("float", 0.5, 5.0),
    "max_open_positions":   ("int",   1,   5),
}

DEFAULT_PRESETS = {
    "default": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "top10": [
        "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
        "DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","SUIUSDT",
    ],
    "top20": [
        "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
        "DOGEUSDT","ADAUSDT","LINKUSDT","AVAXUSDT","SUIUSDT",
        "LTCUSDT","BCHUSDT","DOTUSDT","TRXUSDT","UNIUSDT",
        "ETCUSDT","ATOMUSDT","NEARUSDT","ICPUSDT","AAVEUSDT",
    ],
}

# Jinja2 özel filtresi
@app.template_filter("fmt_volume")
def fmt_volume_filter(vol: float) -> str:
    return um.fmt_volume(vol)


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


def parse_setting(name: str, raw: str | None) -> int | float | None:
    if raw is None:
        return None
    kind, lo, hi = SETTING_RULES[name]
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
    atomic_write_json(PID_PATH, {"pid": pid, "started_at": datetime.now(timezone.utc).isoformat()})


def bot_running() -> bool:
    return bool(find_bot_pids())


def start_bot() -> tuple[bool, str]:
    with CONFIG_LOCK:
        pids = find_bot_pids()
        if pids:
            return False, "Bot zaten çalışıyor; ikinci süreç başlatılmadı."
        if not BOT_PATH.exists():
            return False, "Bot dosyası bulunamadı."
        try:
            out = BOT_OUTPUT.open("a", encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, str(BOT_PATH)],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
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
            if PID_PATH.exists():
                PID_PATH.unlink(missing_ok=True)
            return False, "Uygulamanın başlattığı çalışan bir bot bulunamadı."
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
        lines = [ln.strip() for ln in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
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
    state = state or {}
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
    cfg = config or DEFAULT_CONFIG
    labels: dict[str, tuple[str, str, str]] = {
        "minimum_score":         ("Minimum Sinyal Skoru",      "number", "1"),
        "scan_seconds":          ("Tarama Aralığı (sn)",        "number", "1"),
        "risk_per_trade_pct":    ("İşlem Başına Risk (%)",       "number", "0.1"),
        "daily_loss_limit_pct":  ("Günlük Zarar Limiti (%)",    "number", "0.1"),
        "max_consecutive_losses":("Maks. Ardışık Zarar",        "number", "1"),
        "reward_risk_ratio":     ("Ödül / Risk Oranı",          "number", "0.1"),
        "atr_stop_multiplier":   ("ATR Stop Çarpanı",           "number", "0.1"),
        "max_open_positions":    ("Maks. Açık Pozisyon",        "number", "1"),
    }
    fields = []
    for name, (kind, lo, hi) in SETTING_RULES.items():
        label, inp_type, step = labels[name]
        fields.append({
            "name": name, "label": label,
            "input_type": inp_type, "min": lo, "max": hi, "step": step,
            "value": cfg.get(name, DEFAULT_CONFIG[name]),
        })
    return fields


# ══════════════════════════════════════════════════════════════════════════════
# Akıllı seçim yardımcıları
# ══════════════════════════════════════════════════════════════════════════════

def build_smart_context(config: dict[str, Any] | None) -> dict[str, Any]:
    smart_cfg    = um.get_smart_config()
    suggestions  = smart_cfg.get("last_suggestions", [])
    change_log   = um.get_smart_log()[:20]
    last_ts      = smart_cfg.get("last_analysis_time")
    last_str     = (datetime.fromisoformat(last_ts).strftime("%Y-%m-%d %H:%M UTC")
                    if last_ts else None)

    # Performans karşılaştırması
    state, _ = read_json(STATE_PATH)
    trades   = (state or {}).get("trades", [])
    if not isinstance(trades, list):
        trades = []
    manual_list = smart_cfg.get("manual_list")
    perf = um.get_performance_comparison(trades, manual_list)

    return {
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
    }, perf


def render_dashboard(message: str | None = None, message_type: str = "success"):
    config, config_error = load_config()
    status, state_error  = build_status()
    safe_config          = config or DEFAULT_CONFIG
    smart, perf          = build_smart_context(safe_config)
    return render_template(
        "dashboard.html",
        config=safe_config, status=status,
        setting_fields=setting_fields(config),
        config_error=config_error, state_error=state_error,
        message=message, message_type=message_type,
        smart=smart, perf=perf,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Rotalar — mevcut panel
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
            return render_dashboard(f"Hata: {name} değeri {lo} ile {hi} arasında olmalıdır.", "error")
        updates[name] = parsed
    ok, msg = update_config(updates)
    if ok and bot_running():
        msg += " Değişikliklerin etkili olması için botu yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/coins/add")
def add_coin():
    sym = normalize_symbol(request.form.get("symbol"))
    cfg, err = load_config()
    if err or cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    syms = [str(s) for s in cfg.get("symbols", [])]
    if sym is None:
        return render_dashboard("Hata: Sembol yalnızca A-Z ve 0-9 içermeli ve USDT ile bitmelidir.", "error")
    if sym in syms:
        return render_dashboard(f"Hata: {sym} zaten listede.", "error")
    ok, msg = save_symbols(syms + [sym])
    if ok and bot_running():
        msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
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
        return render_dashboard("Hata: En az bir coin listede kalmalıdır.", "error")
    ok, msg = save_symbols([s for s in syms if s != sym])
    if ok and bot_running():
        msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
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
    idx = syms.index(sym)
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
    name  = request.form.get("preset")
    syms  = DEFAULT_PRESETS.get(name or "")
    if syms is None:
        return render_dashboard("Hata: Geçersiz hazır liste.", "error")
    ok, msg = save_symbols(list(syms))
    if ok:
        msg = "Hazır coin listesi başarıyla kaydedildi."
        if bot_running():
            msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/bot/start")
def bot_start():
    ok, msg = start_bot()
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/bot/stop")
def bot_stop():
    ok, msg = stop_bot()
    return render_dashboard(msg, "success" if ok else "error")


# ══════════════════════════════════════════════════════════════════════════════
# Rotalar — Akıllı Coin Seçimi
# ══════════════════════════════════════════════════════════════════════════════

VALID_MODES = {"MANUEL", "ONERI", "OTOMATIK"}

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
    if mode not in VALID_MODES:
        return render_dashboard("Hata: Geçersiz mod.", "error")
    cfg = um.get_smart_config()
    # Mevcut mod MANUEL ise symbols listesini kaydet
    main_cfg, _ = load_config()
    if cfg.get("mode") == "MANUEL" and main_cfg and mode != "MANUEL":
        cfg["manual_list"] = list(main_cfg.get("symbols", []))
    cfg["mode"] = mode
    um.save_smart_config(cfg)
    mode_label = {"MANUEL": "Manuel", "ONERI": "Öneri", "OTOMATIK": "Otomatik"}[mode]
    msg = f"Mod {mode_label} olarak ayarlandı."
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
        raw = raw.strip()
        try:
            val: int | float = int(raw) if kind == "int" else float(raw)
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
        return render_dashboard("Analiz zaten çalışıyor. Lütfen tamamlanmasını bekleyin.", "error")
    main_cfg, _ = load_config()
    current = list((main_cfg or DEFAULT_CONFIG).get("symbols", []))
    smart_cfg = um.get_smart_config()
    started = um.trigger_analysis(current, smart_cfg, apply_if_auto=False)
    if started:
        return render_dashboard("Analiz başlatıldı. Sonuçlar birkaç dakika içinde görünür.", "success")
    return render_dashboard("Analiz başlatılamadı; zaten çalışıyor olabilir.", "error")


@app.post("/smart/apply")
def apply_smart_suggestions():
    smart_cfg   = um.get_smart_config()
    suggestions = smart_cfg.get("last_suggestions", [])
    if not suggestions:
        return render_dashboard("Hata: Uygulanacak öneri bulunamadı. Önce analiz çalıştırın.", "error")
    main_cfg, err = load_config()
    if err or main_cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    current  = list(main_cfg.get("symbols", []))
    to_add, to_remove = um.compute_auto_changes(suggestions, current, smart_cfg)
    if not to_add and not to_remove:
        return render_dashboard("Öneriler uygulandı; değişiklik gerekmedi.", "success")
    ok, msg = um.apply_auto_changes(to_add, to_remove, smart_cfg, smart_cfg.get("mode", "ONERI"))
    um.save_smart_config(smart_cfg)
    if ok and bot_running():
        msg += " Bot çalışıyor; değişikliklerin etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


@app.post("/smart/restore")
def restore_manual_list():
    smart_cfg   = um.get_smart_config()
    manual_list = smart_cfg.get("manual_list")
    if not manual_list:
        return render_dashboard("Hata: Kaydedilmiş manuel liste bulunamadı.", "error")
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
        return render_dashboard("Hata: Geçersiz sembol.", "error")
    smart_cfg = um.get_smart_config()
    pinned    = list(smart_cfg.get("pinned", []))
    if sym in pinned:
        pinned.remove(sym)
        msg = f"{sym} sabitlemesi kaldırıldı."
    else:
        pinned.append(sym)
        msg = f"{sym} sabitlendi; otomatik çıkarma engellendi."
    smart_cfg["pinned"] = pinned
    um.save_smart_config(smart_cfg)
    return render_dashboard(msg, "success")


@app.post("/smart/coin-action")
def smart_coin_action():
    sym    = normalize_symbol(request.form.get("symbol"))
    action = (request.form.get("action") or "").strip().lower()
    if sym is None or action not in {"add", "remove"}:
        return render_dashboard("Hata: Geçersiz sembol veya işlem.", "error")
    cfg, err = load_config()
    if err or cfg is None:
        return render_dashboard(err or "Ayar dosyası okunamadı.", "error")
    syms = [str(s) for s in cfg.get("symbols", [])]
    if action == "add":
        if sym in syms:
            return render_dashboard(f"Hata: {sym} zaten listede.", "error")
        ok, msg = save_symbols(syms + [sym])
    else:
        if sym not in syms:
            return render_dashboard(f"Hata: {sym} listede bulunamadı.", "error")
        if len(syms) <= 1:
            return render_dashboard("Hata: En az bir coin listede kalmalıdır.", "error")
        ok, msg = save_symbols([s for s in syms if s != sym])
    if ok and bot_running():
        msg += " Bot çalışıyor; etkili olması için yeniden başlatın."
    return render_dashboard(msg, "success" if ok else "error")


# ══════════════════════════════════════════════════════════════════════════════
# API uç noktaları
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/status")
def api_status():
    status, err = build_status()
    resp = dict(status)
    resp["error"] = err
    # position ve trades hassas veri içermez ama trade detaylarını çıkart
    resp.pop("trades", None)
    return resp


@app.get("/api/smart/status")
def api_smart_status():
    smart_cfg = um.get_smart_config()
    return {
        "running":          smart_cfg.get("analysis_running", False) or um.analysis_status["running"],
        "mode":             smart_cfg.get("mode", "MANUEL"),
        "last_analysis":    smart_cfg.get("last_analysis_time"),
        "candidate_count":  smart_cfg.get("candidate_count", 0),
        "next_analysis":    um.next_analysis_str(smart_cfg),
        "error":            um.analysis_status.get("error"),
    }


@app.get("/favicon.ico")
def favicon():
    return "", 204


# ══════════════════════════════════════════════════════════════════════════════
# Başlangıç
# ══════════════════════════════════════════════════════════════════════════════

def _get_main_config() -> dict[str, Any]:
    cfg, _ = load_config()
    return cfg or DEFAULT_CONFIG


if __name__ == "__main__":
    # Otomatik döngüyü yalnızca ana süreçte başlat
    um.start_auto_loop(_get_main_config)
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
