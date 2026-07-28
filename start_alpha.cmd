@echo off
rem ============================================================
rem  Alpha Intelligence OS - Baslat
rem  Tum akil launcher_windows.py icindedir. Bu dosya yalnizca:
rem  1) kendi klasorunu proje koku yapar (%~dp0 - sabit yol YOK)
rem  2) dogru Python'u secer (.venv oncelikli; yoksa yalniz
rem     bootstrap icin py/python)
rem  3) hata durumunda pencereyi ACIK tutar ve exit code gosterir
rem  Secret/env degerleri asla yazilmaz. Log: runtime\launcher.log
rem ============================================================
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" "%~dp0launcher_windows.py" %*
    goto :done
)

rem .venv henuz yok: bootstrap'i baslatmak icin sistem launcher'i
rem YALNIZ launcher_windows.py'yi calistirmak icin kullanilir;
rem sunucu daima .venv python ile calisir.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0launcher_windows.py" %*
    goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0launcher_windows.py" %*
    goto :done
)
echo HATA: Python 3.11+ bulunamadi. Lutfen python.org'dan kurun.
set errorlevel=9009

:done
if not "%errorlevel%"=="0" (
    echo.
    echo Baslatma basarisiz oldu. Cikis kodu: %errorlevel%
    echo Ayrinti: runtime\launcher.log
    pause
)
endlocal
