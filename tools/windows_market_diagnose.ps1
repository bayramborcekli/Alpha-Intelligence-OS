# =====================================================================
# ALPHA INTELLIGENCE OS — WINDOWS PİYASA VERİSİ TEŞHİS PAKETİ (SALT OKUNUR)
# Kullanım (proje kökünde, PowerShell):
#   powershell -ExecutionPolicy Bypass -File tools\windows_market_diagnose.ps1
# Çıktıyı olduğu gibi kopyalayıp geri gönderin.
# HİÇBİR ŞEY DEĞİŞTİRMEZ: dosya yazmaz, .env'e dokunmaz, emir oluşturmaz,
# yalnız public GET + yerel GET + süreç/dosya okuması yapar.
# =====================================================================
$ErrorActionPreference = "Continue"
Write-Host "===== FAZ 0 — GIT KİMLİĞİ ====="
git status --short
git branch --show-current
git rev-parse HEAD
git log -1 --oneline

Write-Host "`n===== FAZ 1 — ÇALIŞAN SÜREÇ KİMLİĞİ ====="
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match "python" } |
  Select-Object ProcessId, ExecutablePath, CommandLine |
  Format-List
Write-Host "-- Dinlenen portlar (python) --"
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName -match "python" } |
  Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table

Write-Host "`n===== FAZ 1b — .venv PYTHON İLE IMPORT YOLLARI ====="
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "UYARI: .venv yok — sistem python KULLANILMAYACAK, bunu rapor edin."; }
else {
& $py -c @"
import sys, os, hashlib, json
print('sys.executable =', sys.executable)
print('cwd =', os.getcwd())
import app; print('app.__file__ =', app.__file__)
sys.path.insert(0, 'alpha20_v1')
import dual_model; print('dual_model.__file__ =', dual_model.__file__)
rp = dual_model.RUNTIME_PATH
print('RUNTIME_PATH =', rp.resolve())
if rp.exists():
    b = rp.read_bytes()
    print('runtime sha256 =', hashlib.sha256(b).hexdigest())
    print('runtime mtime  =', os.path.getmtime(rp))
    rt = json.loads(b or b'{}')
    print('core_list =', len(rt.get('core_list') or []),
          '| opp_list =', len(rt.get('opportunity_list') or []))
    print('last_refresh =', rt.get('last_refresh'))
    print('last_error =', (str(rt.get('last_error'))[:300]))
else:
    print('RUNTIME DOSYASI YOK')
"@
}

Write-Host "`n===== FAZ 2A — curl.exe (Windows TLS deposu) ====="
foreach ($u in @(
  "https://api.binance.com/api/v3/ping",
  "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
  "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2")) {
  Write-Host "-- $u"
  curl.exe -s -m 15 -o NUL -w "HTTP %{http_code}  sure=%{time_total}s  ct=%{content_type}`n" "$u"
}

Write-Host "`n===== FAZ 2B — AYNI .venv PYTHON (uygulamanın HTTP yolu) ====="
if (Test-Path $py) {
& $py -c @"
import ssl, os, time
print('proxy env =', {k: 'TANIMLI' for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy') if os.environ.get(k)} or 'yok')
try:
    import truststore; truststore.inject_into_ssl(); print('truststore = AKTIF')
except Exception as e:
    print('truststore = PASIF:', e)
import requests, certifi
print('certifi =', certifi.where())
for u in ('https://api.binance.com/api/v3/ping',
          'https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT',
          'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2'):
    t = time.time()
    try:
        r = requests.get(u, timeout=15)
        print('PASS', r.status_code, round(time.time()-t,2), 's',
              r.headers.get('content-type'), '|', r.text[:80].replace(chr(10),' '))
    except Exception as e:
        print('FAIL', type(e).__name__, '|', str(e)[:300])
"@
}

Write-Host "`n===== FAZ 2C — YEREL UYGULAMA API ====="
# Portu FAZ 1'deki listeden alın; varsayılan 8080 ve 5000 denenir.
foreach ($port in @(8080, 5000)) {
  Write-Host "-- port $port /health"
  curl.exe -s -m 5 "http://127.0.0.1:$port/health"
  Write-Host "`n-- port $port /api/dual-model/state (ozet)"
  $body = curl.exe -s -m 10 "http://127.0.0.1:$port/api/dual-model/state"
  if ($body) {
    $j = $body | ConvertFrom-Json
    $d = $j.data
    Write-Host ("core=" + $d.core_list.Count + " opp=" + $d.opportunity_list.Count +
      " last_refresh=" + $d.last_refresh + " last_error=" + $d.last_error +
      " open=" + $d.counters.total_open)
  } else { Write-Host "CEVAP YOK" }
}

Write-Host "`n===== FAZ 3 — SUNUCU LOG KUYRUĞU (varsa) ====="
foreach ($lg in @("alpha20_v1\alpha20.log", "alpha20_v1\bot_process.log")) {
  if (Test-Path $lg) { Write-Host "-- $lg (son 25 satır)"; Get-Content $lg -Tail 25 }
}
Write-Host "`n===== TESHIS BITTI — çıktının tamamını geri gönderin ====="
