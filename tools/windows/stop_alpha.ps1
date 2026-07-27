# ============================================================
#  Alpha Intelligence OS — güvenli durdurma
#  Mission 2400 Agent 01
#
#  YALNIZ başlatıcının runtime\alpha.pid dosyasına kaydettiği ve
#  komut satırında serve_windows.py geçen süreci durdurur.
#  İlgisiz python.exe süreçlerine asla dokunmaz; imaj adına göre
#  toplu süreç öldürme KULLANILMAZ.
# ============================================================
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$LogFile = Join-Path $RuntimeDir "launcher.log"
$PidFile = Join-Path $RuntimeDir "alpha.pid"

function Write-Log([string]$Message) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Add-Content -Path $LogFile -Value "$stamp  $Message"
}

Write-Log "Durdurma istendi."

if (-not (Test-Path $PidFile)) {
    Write-Log "PID dosyasi yok — durdurulacak surec kaydi bulunamadi."
    Write-Host "Calisan Alpha Intelligence kaydi bulunamadi (runtime\alpha.pid yok)."
    exit 0
}

# Başlatıcının yazdığı meta veri: pid + start_ticks + tam betik yolu.
# ÜÇÜ BİRDEN eşleşmeden hiçbir sürece dokunulmaz (PID yeniden
# kullanımı / benzer adlı betik çarpışmalarına karşı).
try {
    $Meta = Get-Content $PidFile -Raw | ConvertFrom-Json
    $AppPid = [int]$Meta.pid
} catch {
    Write-Log "PID meta verisi okunamadi — DOKUNULMADI."
    Write-Host "runtime\alpha.pid bozuk; guvenlik geregi surec durdurulmadi."
    exit 1
}

$Proc = Get-Process -Id $AppPid -ErrorAction SilentlyContinue
if (-not $Proc) {
    Write-Log "PID $AppPid artik yasamiyor — bayat kilit temizlendi."
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    exit 0
}

# 1) Başlama zamanı birebir aynı mı? (yeniden kullanılan PID'de farklıdır)
if ($Proc.StartTime.Ticks -ne [long]$Meta.start_ticks) {
    Write-Log "PID $AppPid baslama zamani eslesmiyor (PID yeniden kullanilmis) — DOKUNULMADI."
    Write-Host "Kayitli PID baska bir surece ait; guvenlik geregi dokunulmadi."
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    exit 1
}

# 2) Komut satırı BU projenin serve_windows.py TAM YOLUNU içeriyor mu?
$Cim = Get-CimInstance Win32_Process -Filter "ProcessId = $AppPid" -ErrorAction SilentlyContinue
$ExpectedScript = Join-Path $ProjectRoot "serve_windows.py"
if (-not $Cim -or $Cim.CommandLine -notmatch [regex]::Escape($ExpectedScript)) {
    Write-Log "PID $AppPid bu projenin sunucusu degil — DOKUNULMADI."
    Write-Host "Kayitli PID bu projeye ait degil; guvenlik geregi dokunulmadi."
    exit 1
}

# Kibarca durdur (alt süreçleriyle birlikte), gerekirse zorla.
Write-Log "Surec $AppPid durduruluyor."
taskkill /PID $AppPid /T | Out-Null
Start-Sleep -Seconds 3
if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
    taskkill /PID $AppPid /T /F | Out-Null
    Write-Log "Zorla durduruldu."
} else {
    Write-Log "Duzgun durduruldu."
}

# Kilit/PID dosyaları yalnız kapanıştan SONRA temizlenir.
Remove-Item $PidFile -ErrorAction SilentlyContinue
Write-Log "Durdurma tamamlandi."
Write-Host "Alpha Intelligence OS durduruldu."
