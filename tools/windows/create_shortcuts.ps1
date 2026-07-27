# ============================================================
#  Alpha Intelligence OS — masaüstü kısayollarını oluştur
#  Mission 2400 Agent 01
#
#  Çalıştırma (tek sefer, yönetici gerekmez):
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows\create_shortcuts.ps1
#
#  Oluşturur:
#    Masaüstü\Alpha Intelligence OS.lnk           -> start_alpha.cmd
#    Masaüstü\Alpha Intelligence OS — Durdur.lnk  -> stop_alpha.cmd
# ============================================================
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Desktop = [Environment]::GetFolderPath("Desktop")
$Shell = New-Object -ComObject WScript.Shell

# Proje ikonu varsa kullan; yoksa cmd varsayılanı kalır.
$Icon = $null
foreach ($cand in @("static\favicon.ico", "static\img\alpha.ico")) {
    $p = Join-Path $ProjectRoot $cand
    if (Test-Path $p) { $Icon = $p; break }
}

function New-AlphaShortcut([string]$Name, [string]$Target) {
    $lnk = $Shell.CreateShortcut((Join-Path $Desktop "$Name.lnk"))
    $lnk.TargetPath = (Join-Path $ProjectRoot $Target)
    $lnk.WorkingDirectory = $ProjectRoot          # "Baslatma konumu"
    $lnk.Description = "Alpha Intelligence OS"
    if ($Icon) { $lnk.IconLocation = $Icon }
    $lnk.Save()
    Write-Host "Olusturuldu: $Name"
}

New-AlphaShortcut "Alpha Intelligence OS" "start_alpha.cmd"
New-AlphaShortcut "Alpha Intelligence OS — Durdur" "stop_alpha.cmd"
Write-Host "Masaustu kisayollari hazir."
