@echo off
rem ============================================================
rem  Alpha Intelligence OS - Durdur
rem  Yalnizca runtime\alpha.pid icindeki, BU clone'a ait oldugu
rem  dogrulanan sureci durdurur; ilgisiz python sureclerine ve
rem  eski clone'lara ASLA dokunmaz. (Mantik: launcher_windows.py)
rem ============================================================
setlocal
cd /d "%~dp0"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" "%~dp0launcher_windows.py" --stop
) else (
    where py >nul 2>nul && ( py -3 "%~dp0launcher_windows.py" --stop ) || ( python "%~dp0launcher_windows.py" --stop )
)
if not "%errorlevel%"=="0" (
    echo.
    echo Durdurma basarisiz. Cikis kodu: %errorlevel%
    echo Ayrinti: runtime\launcher.log
    pause
)
endlocal
