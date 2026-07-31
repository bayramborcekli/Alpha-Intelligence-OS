# =====================================================================
# ALPHA INTELLIGENCE OS — WINDOWS GUVENLI TESHIS v2 (SALT-OKUNUR)
# Sozlesme:
#   - Hicbir dosyaya YAZMAZ, hicbir dosyayi silmez/olusturmaz.
#   - 'import app' / 'import dual_model' / uygulama modulu calistirmaz.
#   - Yalnizca /health endpoint'ini cagirir (kaynak kodda saf salt-okunur
#     oldugu kanitlandi: sadece status/uptime/pid dondurur, auth istemez).
#   - Secret, env degeri, token, ham komut satiri, ham log YAZDIRMAZ.
#   - DPAPI deposunu COZMEZ; yalnizca dosya varligi/tarihi raporlanir.
#   - Servisi baslatmaz/durdurmaz/restart etmez.
# Kullanim:  powershell -ExecutionPolicy Bypass -File tools\windows_market_diagnose_v2.ps1
# =====================================================================
$ErrorActionPreference = "Continue"
function Sec($t) { Write-Output ""; Write-Output ("==== " + $t + " ====") }

# ---- FAZ 0: proje koku ve calisma duzeni dogrulamasi ----
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Sec "FAZ 0 — PROJE KOKU"
Write-Output ("PROJECT_ROOT: " + $Root)
$Venv = Join-Path $Root ".venv\Scripts\python.exe"
$Serve = Join-Path $Root "serve_windows.py"
Write-Output ("VENV_PYTHON_EXISTS: " + (Test-Path $Venv))
Write-Output ("SERVE_WINDOWS_EXISTS: " + (Test-Path $Serve))
if (-not (Test-Path $Venv)) { Write-Output "SONUC: PYTHON_RUNTIME=WRONG_RUNTIME_OR_MISSING — .venv bulunamadi, devam eden testler sinirli." }
if (Test-Path $Venv) { Write-Output ("VENV_PYTHON_VERSION: " + (& $Venv --version 2>&1)) }

Sec "GIT DURUMU (salt-okunur)"
Push-Location $Root
try {
  Write-Output ("WINDOWS_GIT_HEAD: " + (git rev-parse HEAD 2>&1))
  Write-Output ("WINDOWS_GIT_BRANCH: " + (git rev-parse --abbrev-ref HEAD 2>&1))
} finally { Pop-Location }

# ---- Surec ve port (yalniz guvenli alanlar; komut satiri YOK) ----
Sec "SUNUCU SURECI VE PORTLAR (5000/8080)"
$found = $false
foreach ($port in 5000, 8080) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    $found = $true
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    Write-Output ("PORT " + $port + " -> PID=" + $c.OwningProcess +
      " EXE=" + $p.Path + " START=" + $p.StartTime)
  }
  if (-not $conns) { Write-Output ("PORT " + $port + ": DINLEYEN YOK") }
}
if (-not $found) { Write-Output "SERVER_PROCESS: FAIL (5000/8080 uzerinde dinleyen surec yok)" }
# Duplicate kontrolu: ayni exe yolundan kac python sureci var (komut satiri gizli)
$pyProcs = Get-Process | Where-Object { $_.Path -like "*\.venv\Scripts\python.exe" }
Write-Output ("VENV_PYTHON_PROCESS_COUNT: " + (@($pyProcs).Count))
foreach ($pp in $pyProcs) { Write-Output ("  PID=" + $pp.Id + " START=" + $pp.StartTime) }

# ---- Modul yollari: STATIK cozum (import YOK) ----
Sec "MODUL YOLLARI (statik, calistirma yok)"
$appPath = Join-Path $Root "app.py"
$dmPath  = Join-Path $Root "alpha20_v1\dual_model.py"
Write-Output ("DIAGNOSTIC_APP_PATH: " + $(if (Test-Path $appPath) { $appPath } else { "BULUNAMADI" }))
Write-Output ("DIAGNOSTIC_DUAL_MODEL_PATH: " + $(if (Test-Path $dmPath) { $dmPath } else { "BULUNAMADI" }))
$legacy = Join-Path $Root "alpha20.py"
Write-Output ("LEGACY_ROOT_ALPHA20_PRESENT: " + (Test-Path $legacy))
Write-Output "RUNNING_IMPORT_PATHS: UNVERIFIED (calisan surecin import yollari bu teshisle kanitlanamaz)"

# ---- Durum dosyalari: salt-okunur JSON okuma (.venv python, yalniz stdlib json) ----
Sec "DURUM DOSYALARI (salt-okunur)"
if (Test-Path $Venv) {
  $py = @'
import json, os, datetime
root = os.environ["AIOS_ROOT"]
def mt(p):
    try: return datetime.datetime.utcfromtimestamp(os.path.getmtime(p)).isoformat() + "Z"
    except OSError: return "YOK"
def load(p):
    try:
        with open(p, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e: return {"__error__": type(e).__name__}
op = load(os.path.join(root, "alpha20_v1", "operation_control_state.json"))
print("OPERATION_STATE:", op.get("automation_state", op.get("__error__", "YOK")))
cfg = load(os.path.join(root, "alpha20_v1", "config.json"))
ad = cfg.get("adaptive_system", {}) if isinstance(cfg, dict) else {}
print("ADAPTIVE_ENABLED:", ad.get("enabled"), "MODE:", ad.get("mode"))
urp = os.path.join(root, "alpha20_v1", "universe_runtime.json")
rt = load(urp); sm = rt.get("smart", {}) if isinstance(rt, dict) else {}
print("UNIVERSE_RUNTIME_MTIME:", mt(urp))
print("SCHEDULER_REFRESH:", json.dumps(sm.get("scheduler_refresh")))
print("LAST_ANALYSIS_TIME:", sm.get("last_analysis_time"))
print("DYNAMIC_SYMBOLS:", rt.get("dynamic_symbols"))
dmp = os.path.join(root, "alpha20_v1", "dual_model_runtime.json")
d = load(dmp)
print("DUAL_RUNTIME_MTIME:", mt(dmp))
print("CORE_COUNT:", len(d.get("core_list", []) or []))
print("OPPORTUNITY_COUNT:", len(d.get("opportunity_list", []) or []))
print("RUNTIME_LAST_REFRESH:", d.get("last_refresh"))
le = d.get("last_error")
print("RUNTIME_LAST_ERROR:", (str(le)[:160] if le else None))
# DPAPI deposu: yalniz varlik/tarih; icerik ASLA okunmaz/cozulmez
for cand in ("alpha20_v1\\secure_credentials.dat", "alpha20_v1\\credentials.enc", "secure_store\\credentials.dat"):
    p = os.path.join(root, *cand.split("\\"))
    if os.path.exists(p): print("DPAPI_STORE:", cand, "EXISTS mtime=", mt(p))
print("UTC_NOW:", datetime.datetime.utcnow().isoformat() + "Z")
'@
  $env:AIOS_ROOT = $Root
  $py | & $Venv -
} else { Write-Output "ATLANDI: .venv python yok" }

# ---- Public Binance testi (secret'siz; .venv python + requests) ----
Sec "PUBLIC BINANCE (DNS/TLS/HTTP — secret kullanilmaz)"
try { $dns = Resolve-DnsName api.binance.com -ErrorAction Stop | Select-Object -First 1
      Write-Output ("PUBLIC_DNS: PASS " + $dns.IPAddress) }
catch { Write-Output ("PUBLIC_DNS: FAIL " + $_.Exception.GetType().Name) }
if (Test-Path $Venv) {
  $py2 = @'
import ssl, sys
try:
    import truststore; truststore.inject_into_ssl(); ts = "INJECTED"
except Exception as e: ts = "UNAVAILABLE:" + type(e).__name__
print("TRUSTSTORE:", ts)
try:
    import requests
except Exception as e:
    print("REQUESTS_IMPORT: FAIL", type(e).__name__); sys.exit(0)
for url in ("https://api.binance.com/api/v3/ping",
            "https://api.binance.com/api/v3/time",
            "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1"):
    try:
        r = requests.get(url, timeout=15)
        print("GET", url.split("/api/")[1].split("?")[0], "->", r.status_code)
    except Exception as e:
        print("GET", url.split("/api/")[1].split("?")[0], "-> HATA:", type(e).__name__)
'@
  $py2 | & $Venv -
} else { Write-Output "ATLANDI: .venv python yok" }

# ---- Yerel /health (kaynak kodda saf salt-okunur kanitli; auth istemez) ----
Sec "YEREL /health (salt-okunurlugu kanitli tek endpoint)"
foreach ($port in 5000, 8080) {
  try {
    $r = Invoke-WebRequest -Uri ("http://127.0.0.1:" + $port + "/health") -UseBasicParsing -TimeoutSec 8
    Write-Output ("PORT " + $port + " /health -> HTTP " + $r.StatusCode + " GOVDE: " + $r.Content)
  } catch { Write-Output ("PORT " + $port + " /health -> ERISILEMEDI (" + $_.Exception.GetType().Name + ")") }
}

Sec "BITTI — ciktinin tamamini kopyalayip gonderin"
Write-Output "NOT: Bu betik hicbir dosya yazmadi; private-auth testi bilerek YAPILMADI (PRIVATE_AUTH: UNVERIFIED)."
