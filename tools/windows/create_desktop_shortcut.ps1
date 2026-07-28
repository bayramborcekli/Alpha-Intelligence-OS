# ============================================================
#  Alpha Intelligence OS - Masaustu kisayolu olustur/guncelle
#  Target ve Start In DAIMA bu script'in bulundugu clone'a
#  isaret eder; eski clone yollari otomatik olarak duzelir.
#  Sabit surucu/kullanici yolu YOKTUR.
# ============================================================
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$StartCmd = Join-Path $ProjectRoot "start_alpha.cmd"
if (-not (Test-Path $StartCmd)) {
    Write-Error "start_alpha.cmd bulunamadi: proje koku hatali."
    exit 1
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$LinkPath = Join-Path $Desktop "Alpha Intelligence OS.lnk"

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($LinkPath)   # varsa gunceller
$Shortcut.TargetPath = $StartCmd
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "Alpha Intelligence OS (PAPER)"
$Shortcut.Save()

Write-Output "Kisayol guncellendi: $LinkPath -> $StartCmd"
