@echo off
rem ============================================================
rem  Alpha Intelligence OS - Windows Kurulum (tek giris noktasi)
rem  - Python tespiti (py -3 / python; PATH DEGISTIRILMEZ)
rem  - .venv olusturma + bagimlilik kurulumu (idempotent)
rem  - masaustu kisayolu
rem  - smoke test
rem  Gercek secret OLUSTURMAZ; .env'i kullanici koyar.
rem ============================================================
setlocal
cd /d "%~dp0"
echo Alpha Intelligence OS kurulumu basliyor...

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" "%~dp0launcher_windows.py" --install
    goto :done
)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0launcher_windows.py" --install
    goto :done
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0launcher_windows.py" --install
    goto :done
)
echo HATA: Python 3.11+ bulunamadi. Lutfen python.org'dan kurun
echo (kurulumda "py launcher" secenegini isaretleyin).
set errorlevel=9009

:done
echo.
if "%errorlevel%"=="0" (
    echo Kurulum tamamlandi. Masaustundeki "Alpha Intelligence OS"
    echo kisayoluyla baslatabilirsiniz.
) else (
    echo Kurulum basarisiz. Cikis kodu: %errorlevel%
    echo Ayrinti: runtime\launcher.log
)
pause
endlocal
