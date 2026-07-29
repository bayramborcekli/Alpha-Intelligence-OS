# ============================================================
#  Alpha Intelligence OS - Windows hazirlik (git + python/.venv)
#  SETUP_AND_START_WINDOWS.cmd tarafindan cagirilir.
#  Secret uretmez/basmaz. SSL dogrulamasi kapatilmaz.
# ============================================================
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[1/3] Git araniyor..."
$git = $null
$cmd = Get-Command git -ErrorAction SilentlyContinue
if ($cmd) { $git = $cmd.Source }
if (-not $git) {
    $candidates = @(
        "$env:ProgramFiles\Git\cmd\git.exe",
        "${env:ProgramFiles(x86)}\Git\cmd\git.exe"
    )
    $candidates += Get-ChildItem -Path "$env:LOCALAPPDATA\GitHubDesktop" -Filter git.exe -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { $git = $c; break } }
}
if (-not $git) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "Git bulunamadi - winget ile kuruluyor (onay gerekebilir)..."
        winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
        foreach ($c in @("$env:ProgramFiles\Git\cmd\git.exe", "${env:ProgramFiles(x86)}\Git\cmd\git.exe")) {
            if (Test-Path $c) { $git = $c; break }
        }
    }
}
if ($git) {
    Write-Host "Git: $git"
    & $git -C $Root status --porcelain=v1 2>$null | Where-Object { $_ -match '^[ MARC][MD ]' -and $_ -notmatch '\.env|state\.json|trade_history|accounts\.json|risk_history' } | Tee-Object -Variable dirty | Out-Null
    if ($dirty) {
        Write-Host "UYARI: Kaynak kodda yerel degisiklik var - git pull ATLANDI (uzerine yazilmadi)."
        Write-Host "Detay: git status ile bakin. Mevcut kodla devam ediliyor."
    } else {
        & $git -C $Root pull
        if ($LASTEXITCODE -ne 0) { Write-Host "UYARI: git pull basarisiz - mevcut kodla devam." }
    }
    $head = (& $git -C $Root rev-parse --short HEAD 2>$null)
    Write-Host "HEAD: $head"
} else {
    Write-Host "UYARI: Git kurulamadi/bulunamadi - guncelleme atlandi, mevcut kodla devam."
}

Write-Host "[2/3] Python / .venv hazirlaniyor..."
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { & py -3 -m venv (Join-Path $Root ".venv") }
    else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($python) { & python -m venv (Join-Path $Root ".venv") }
        else { Write-Host "HATA: Python 3.11+ bulunamadi. python.org'dan kurun."; exit 1 }
    }
}
if (-not (Test-Path $venvPy)) { Write-Host "HATA: .venv olusturulamadi."; exit 1 }

Write-Host "[3/3] Paketler guncelleniyor (pip, requirements, certifi, truststore)..."
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt") --quiet
& $venvPy -m pip install --upgrade certifi truststore --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "HATA: paket kurulumu basarisiz."; exit 1 }
Write-Host "Hazirlik tamam."
exit 0
