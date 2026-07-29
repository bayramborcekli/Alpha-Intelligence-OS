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

echo [3/6] Eski Alpha surecleri kapatiliyor (yalniz bu projeye ait olanlar)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'serve_windows|launcher_windows' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul
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
echo.
echo Panel: http://127.0.0.1:5000  (kart FINAL rapordaki renktedir)
pause
