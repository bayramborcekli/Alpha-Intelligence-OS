@echo off
rem Alpha Intelligence OS - ADR-024 tek tik 4h Spot kanit + Windows ekran
rem Yalniz Binance public GET kullanir. Canli emir/transfer/cekme yoktur.
setlocal EnableExtensions
cd /d "%~dp0" || goto :root_error

if not exist "%~dp0paper_profit_research.py" goto :incomplete_package
if not exist "%~dp0paper_profit_strategy.py" goto :incomplete_package
if not exist "%~dp0paper_profit_api.py" goto :incomplete_package
if not exist "%~dp0start_alpha.cmd" goto :incomplete_package

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    if not exist "%~dp0INSTALL_WINDOWS.cmd" goto :incomplete_package
    echo Kurulum hazirlaniyor...
    call "%~dp0INSTALL_WINDOWS.cmd"
    if errorlevel 1 goto :install_error
)

echo.
echo GERCEK BINANCE SPOT 4 SAATLIK KANIT TESTI BASLADI...
"%VENV_PY%" "%~dp0paper_profit_research.py" --years 2
set "RESEARCH_RC=%errorlevel%"
echo.
if "%RESEARCH_RC%"=="0" (
    echo KANIT: PASS - PAPER_PROFIT_V1 adayi ekranda hazir.
) else if "%RESEARCH_RC%"=="1" (
    echo KANIT: REJECTED - basarisiz aday Paper'a baglanmadi.
) else (
    echo KANIT: ERROR - veri/SSL baglantisini kontrol edin.
)

echo.
echo Windows ekrani aciliyor...
call "%~dp0start_alpha.cmd"
if errorlevel 1 goto :start_error
timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:5000/home"
echo.
echo Windows ekrani acildi: http://127.0.0.1:5000/home
goto :finish

:incomplete_package
echo.
echo HATA: Windows paketi eksik veya CMD proje ana klasorunde degil.
echo Bu dosyayi tek basina Indirilenler klasorunden calistirmayin.
echo RUN_PAPER_PROFIT_WINDOWS.cmd ve paper_profit_*.py dosyalari
echo start_alpha.cmd ile ayni proje klasorunde bulunmalidir.
set "RESEARCH_RC=2"
goto :finish

:root_error
echo HATA: Proje klasoru acilamadi.
set "RESEARCH_RC=2"
goto :finish

:install_error
echo.
echo Kurulum tamamlanamadi.
echo Ayrinti: runtime\launcher.log
set "RESEARCH_RC=2"
goto :finish

:start_error
echo.
echo HATA: Windows ekrani baslatilamadi.
echo Ayrinti: runtime\launcher.log
set "RESEARCH_RC=2"

:finish
if not defined RESEARCH_RC set "RESEARCH_RC=2"
echo.
echo Cikis kodu: %RESEARCH_RC%
echo Bu pencereyi kapatmak icin bir tusa basin.
pause >nul
endlocal & exit /b %RESEARCH_RC%
