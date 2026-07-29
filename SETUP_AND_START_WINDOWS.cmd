@echo off
rem ============================================================
rem  Alpha Intelligence OS - TEK GIRIS NOKTASI (cift tiklayin)
rem  Hazirlik (git+python+.venv) -> .env onarimi -> SSL testleri ->
rem  risk kilidi -> sunucu -> health -> istege bagli hesap baglama
rem  -> TEK FINAL raporu. SSL dogrulamasi ASLA kapatilmaz.
rem  Canli emir yolu YOKTUR. Secret'lar ekrana/git'e yazilmaz.
rem ============================================================
setlocal
cd /d "%~dp0"

echo ALPHA INTELLIGENCE OS - TEK TIK KURULUM + BASLATMA
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows_setup.ps1"
if errorlevel 1 (
    echo.
    echo HATA: Hazirlik adimi basarisiz - yukaridaki mesaji okuyun.
    pause
    exit /b 1
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PY%" set "VENV_PY=python"
"%VENV_PY%" "%~dp0windows_setup_flow.py"
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
    echo SONUC: PASS - Runtime karti YESIL. Panel: http://127.0.0.1:5000
) else (
    echo SONUC: FINAL rapordaki ROOT CAUSE satiri tek gercek nedeni soyluyor.
    echo Engel kalkinca bu dosyaya tekrar cift tiklamaniz yeterli.
)
pause
exit /b %RC%
