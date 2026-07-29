@echo off
rem ============================================================
rem  Alpha Intelligence OS - WINDOWS TEK KOMUT KURTARMA
rem  Sirayla: git pull -> kurulum (certifi+truststore) ->
rem  eski python sureclerini kapat -> teshis -> sunucuyu baslat
rem  SSL dogrulamasi ASLA kapatilmaz. Canli emir yolu YOKTUR.
rem ============================================================
setlocal
cd /d "%~dp0"
echo [1/5] Guncel kod cekiliyor (git pull)...
git pull
if errorlevel 1 echo UYARI: git pull basarisiz - mevcut kodla devam ediliyor.

echo [2/5] Kurulum/guncelleme (certifi + truststore dahil)...
call INSTALL_WINDOWS.cmd

echo [3/5] Eski python surecleri kapatiliyor (temiz baslangic)...
taskkill /F /IM python.exe >nul 2>nul
timeout /t 2 /nobreak >nul

echo [4/5] Otomatik teshis calisiyor...
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PY%" set "VENV_PY=python"
"%VENV_PY%" windows_diagnose.py
if errorlevel 1 (
    echo.
    echo TESHIS FAIL VERDI. Yukaridaki ROOT CAUSE satirini okuyun:
    echo  - antivirus HTTPS taramasi ise *.binance.com'u istisna yapin
    echo  - proxy/VPN ise Binance icin kapatin
    echo Sorunu giderdikten sonra bu dosyayi tekrar calistirin.
    pause
    exit /b 1
)

echo [5/5] Sunucu baslatiliyor (serve_windows)...
echo Beklenen loglar: WINDOWS PAPER AUTO ENABLED / AUTO LOOP STARTED /
echo CONTROLLER STARTED / FIRST CYCLE COMPLETED
echo Panel: http://127.0.0.1:5000  (kart 🟢 Calisiyor olmali)
"%VENV_PY%" serve_windows.py
