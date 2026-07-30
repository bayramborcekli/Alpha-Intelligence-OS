@echo off
REM Tam test paketini otomatik bolerek kosar (yanlis yesil korumasi).
REM Tek pytest kosusu ~13.6k testte summary basmadan sessizce olebiliyor;
REM bu sarmalayici tools\run_full_suite.py'yi cagirir: parca summary'si
REM yoksa kosu FAIL sayilir. CI'da dogrudan "python -m pytest tests/"
REM KULLANMAYIN — bu betigi kullanin.
setlocal
cd /d "%~dp0..\.."
python tools\run_full_suite.py %*
set RC=%ERRORLEVEL%
if %RC% NEQ 0 (
  echo.
  echo [FAIL] Test paketi basarisiz veya summary eksik ^(rc=%RC%^).
)
endlocal & exit /b %RC%
