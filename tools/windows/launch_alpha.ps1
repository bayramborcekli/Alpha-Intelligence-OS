# ============================================================
#  Alpha Intelligence OS — Windows başlatıcısı
#  Mission 2400 Agent 01
#
#  Görev sırası:
#    1. Proje kökünü dinamik çöz (sabit sürücü/dizin varsayımı yok).
#    2. Temel gereksinimleri doğrula (Python, giriş noktası, runtime).
#    3. Zaten çalışıyorsa: kopya başlatma, tarayıcıyı aç, çık.
#    4. Çalışmıyorsa: sunucuyu gizli başlat, /health hazır olana dek
#       bekle, ancak o zaman Trading Home'u aç.
#    5. runtime\launcher.log'a SANİTİZE günlük yaz (asla gizli değer
#       yazılmaz; yalnız sabit metinler ve durum kodları).
# ============================================================
$ErrorActionPreference = "Stop"

# --- Yol çözümü (kök = bu dosyanın iki üst dizini) ------------------
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot

$RuntimeDir = Join-Path $ProjectRoot "runtime"
if (-not (Test-Path $RuntimeDir)) { New-Item -ItemType Directory -Path $RuntimeDir | Out-Null }
$LogFile = Join-Path $RuntimeDir "launcher.log"
$PidFile = Join-Path $RuntimeDir "alpha.pid"

$Port = if ($env:ALPHA_PORT) { $env:ALPHA_PORT } else { "5000" }
$HealthUrl = "http://127.0.0.1:$Port/health"
$HomeUrl   = "http://127.0.0.1:$Port/home"

function Write-Log([string]$Message) {
    # SANİTİZE: yalnız sabit mesajlar; ortam içeriği/gizli değer yok.
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $LogFile -Value "$stamp  $Message"
}

function Fail([string]$UserMessage, [string]$Code) {
    Write-Log "HATA kod=$Code"
    [void][System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")
    [System.Windows.Forms.MessageBox]::Show(
        "$UserMessage`n`nAyrinti: runtime\launcher.log",
        "Alpha Intelligence OS") | Out-Null
    exit 1
}

function Test-Ready {
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

Write-Log "Baslatma istendi."

# --- Zaten çalışıyor mu? -------------------------------------------
if (Test-Ready) {
    Write-Log "Zaten calisiyor — kopya baslatilmadi; tarayici aciliyor."
    Start-Process $HomeUrl
    exit 0
}

# --- Ortam doğrulama (yalnız temel gereksinimler) -------------------
if (-not (Test-Path (Join-Path $ProjectRoot "app.py"))) {
    Fail "Uygulama giris noktasi (app.py) bulunamadi. Kisayolun 'Baslatma konumu' proje klasoru olmali." "NO_ENTRYPOINT"
}
if (-not (Test-Path (Join-Path $ProjectRoot "serve_windows.py"))) {
    Fail "serve_windows.py bulunamadi. Proje kopyasi eksik olabilir." "NO_SERVER_ENTRY"
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
    Write-Log "Sanal ortam Python'u kullaniliyor."
} else {
    $Cmd = Get-Command "python" -ErrorAction SilentlyContinue
    if (-not $Cmd) { $Cmd = Get-Command "py" -ErrorAction SilentlyContinue }
    if (-not $Cmd) {
        Fail "Python bulunamadi. Once Python 3.11 kurun ve kurulum kilavuzundaki adimlari izleyin (docs\windows_launcher_tr.md)." "NO_PYTHON"
    }
    $Python = $Cmd.Source
    Write-Log "Sistem Python'u kullaniliyor."
}

# Port başka bir programda mı? (health cevap vermedi ama port dolu)
$PortBusy = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
if ($PortBusy) {
    Fail "Port $Port baska bir program tarafindan kullaniliyor. O programi kapatin veya ALPHA_PORT ortam degiskeniyle farkli port secin." "PORT_BUSY"
}

# --- Başlat ---------------------------------------------------------
Write-Log "Uygulama sureci baslatiliyor."
$ServerScript = Join-Path $ProjectRoot "serve_windows.py"
$Proc = Start-Process -FilePath $Python -ArgumentList "`"$ServerScript`"" `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru

# PID meta verisi: durdurma betiği ÜÇ alanı birden doğrular
# (pid + süreç başlama zamanı + tam betik yolu). Bayat PID yeniden
# kullanılırsa başlama zamanı eşleşmez ve sürece dokunulmaz.
$Meta = @{
    pid         = $Proc.Id
    start_ticks = $Proc.StartTime.Ticks
    script      = $ServerScript
} | ConvertTo-Json -Compress
Set-Content -Path $PidFile -Value $Meta

# --- Hazır olana dek bekle (en çok 60 sn) ---------------------------
$Ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if ($Proc.HasExited) { break }
    if (Test-Ready) { $Ready = $true; break }
}

if (-not $Ready) {
    Write-Log "Hazirlik denetimi BASARISIZ."
    if (-not $Proc.HasExited) { Stop-Process -Id $Proc.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    Fail "Uygulama baslatilamadi. Bozuk bir sayfa acilmadi." "NOT_READY"
}

Write-Log "Hazirlik denetimi OK — tarayici aciliyor."
Start-Process $HomeUrl
Write-Log "Tarayici acildi. PID dosyasi: runtime\alpha.pid"
exit 0
