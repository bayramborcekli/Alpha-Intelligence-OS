@echo off
rem ============================================================
rem  Alpha Intelligence OS - WINDOWS TEK TIK ENTEGRE KURTARMA
rem  Tek akis: git pull -> kurulum -> .env onarimi (onayli) ->
rem  SSL/Binance testleri -> yalniz Alpha sureclerini kapat ->
rem  sunucuyu baslat -> health'i bekle -> tek FINAL raporu.
rem  SSL dogrulamasi ASLA kapatilmaz. Canli emir yolu YOKTUR.
rem ============================================================
setlocal
cd /d "%~dp0"
echo NOT: Yeni tek giris noktasi SETUP_AND_START_WINDOWS.cmd dosyasidir.
echo Bu betik uyumluluk icin calismaya devam eder.
echo.

echo [1/6] Guncel kod cekiliyor (git pull)...
set "GIT_EXE=git"
where git >nul 2>nul
if errorlevel 1 (
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
        set "GIT_EXE=%ProgramFiles%\Git\cmd\git.exe"
    ) else if exist "%ProgramFiles(x86)%\Git\cmd\git.exe" (
        set "GIT_EXE=%ProgramFiles(x86)%\Git\cmd\git.exe"
    ) else (
        set "GIT_EXE="
    )
)
if defined GIT_EXE (
    "%GIT_EXE%" pull
    if errorlevel 1 echo UYARI: git pull basarisiz - mevcut kodla devam ediliyor.
) else (
    echo UYARI: git bulunamadi - guncelleme atlandi, mevcut kodla devam ediliyor.
)

echo [2/6] Kurulum/guncelleme (certifi + truststore dahil)...
call INSTALL_WINDOWS.cmd
if errorlevel 1 (
    echo HATA: Kurulum basarisiz - devam edilemiyor. Yukaridaki hatayi giderin.
    pause
    exit /b 1
)

echo [3/6] Eski Alpha surecleri kapatiliyor (yalniz BU klasordeki proje)...
rem  Klasor yolu PS komutuna GOMULMEZ (bosluk/kesme isareti guvenligi):
rem  cd /d "%~dp0" yukarida yapildi; PS kendi calisma dizininden okur.
rem  ONCE PID dosyasi (.alpha_server.pid) kesin eslesmeyle denenir; bayat/
rem  geri kullanilmis PID'lere karsi ad + komut satiri dogrulanir. Komut
rem  satiri deseni YEDEK yontem olarak her durumda calisir.
powershell -NoProfile -Command "$root=[regex]::Escape((Get-Location).Path + [IO.Path]::DirectorySeparatorChar); $pidFile=Join-Path (Get-Location).Path '.alpha_server.pid'; if (Test-Path $pidFile) { $raw=(Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1); $alphaPid=0; if ([int]::TryParse(('' + $raw).Trim(), [ref]$alphaPid) -and $alphaPid -gt 0) { $p=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $alphaPid) -ErrorAction SilentlyContinue; if ($p -and $p.Name -match '^python(w)?\.exe$' -and $p.CommandLine -match ($root+'.*(serve_windows|launcher_windows)')) { Stop-Process -Id $alphaPid -Force -ErrorAction SilentlyContinue } }; Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match ($root+'.*(serve_windows|launcher_windows)') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul
timeout /t 2 /nobreak >nul

echo [4/6] Otomatik teshis + .env onarimi calisiyor...
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PY%" set "VENV_PY=python"
"%VENV_PY%" windows_diagnose.py
if errorlevel 1 (
    echo.
    echo TESHIS DURDURDU: Binance baglantisi yetersiz (3 sembolden en az
    echo 2'si gerekli). Yukaridaki ROOT CAUSE satirini okuyun:
    echo  - antivirus HTTPS taramasi ise *.binance.com'u istisna yapin
    echo  - proxy/VPN ise Binance icin kapatin
    echo Engel kalkinca bu dosyayi tekrar calistirmaniz yeterli.
    pause
    exit /b 1
)

echo [5/6] Sunucu baslatiliyor (ayri pencerede, tek instance)...
start "Alpha Intelligence OS" "%VENV_PY%" serve_windows.py
echo Beklenen loglar (yeni pencerede): WINDOWS PAPER AUTO ENABLED /
echo AUTO LOOP STARTED / CONTROLLER STARTED / FIRST CYCLE COMPLETED

echo [6/6] Runtime dogrulaniyor (ilk cevrim icin en fazla 120 sn beklenir)...
"%VENV_PY%" windows_diagnose.py --wait-health 120
if errorlevel 1 (
    echo.
    echo SONUC: FINAL rapor yesil DEGIL - yukaridaki ROOT CAUSE satiri tek
    echo gercek nedeni soyluyor. Engel kalkinca bu dosyayi tekrar calistirin.
    pause
    exit /b 1
)
echo.
set "ALPHA_PANEL_PORT=%ALPHA_PORT%"
if not defined ALPHA_PANEL_PORT set "ALPHA_PANEL_PORT=5000"
echo SONUC: PASS - kart 🟢. Panel: http://127.0.0.1:%ALPHA_PANEL_PORT%
pause
