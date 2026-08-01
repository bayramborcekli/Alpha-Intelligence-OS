@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Alpha20 Revize V2 - Windows Dogrulama

set "VERIFY_ROOT=%~dp0"
set "V2_PY=%VERIFY_ROOT%.venv\Scripts\python.exe"
set "VERIFY_LOG=%VERIFY_ROOT%ALPHA20_REVIZE_V2_WINDOWS_VERIFY.log"
set "FAIL_CODE=1"
set "FAIL_REASON=Bilinmeyen hata"

>"%VERIFY_LOG%" echo Alpha20 Revize V2 Windows Dogrulama
>>"%VERIFY_LOG%" echo Proje: %VERIFY_ROOT%
>>"%VERIFY_LOG%" echo Tarih: %DATE% %TIME%
>>"%VERIFY_LOG%" echo.

echo ============================================================
echo  ALPHA20 REVIZE V2 - WINDOWS DOGRULAMA
echo ============================================================
echo Proje: %VERIFY_ROOT%
echo.

echo [0/5] Gerekli dosyalar kontrol ediliyor...
if not exist "%V2_PY%" (
  set "FAIL_CODE=2"
  set "FAIL_REASON=.venv Python bulunamadi. Once INSTALL_WINDOWS.cmd calistirin."
  goto :fail
)
if not exist "%VERIFY_ROOT%scripts\project_preflight.py" (
  set "FAIL_CODE=2"
  set "FAIL_REASON=scripts\project_preflight.py bulunamadi. ZIP'i proje ana klasorune cikarin."
  goto :fail
)
if not exist "%VERIFY_ROOT%app.py" (
  set "FAIL_CODE=2"
  set "FAIL_REASON=app.py bulunamadi. Dosya proje ana klasorunde degil."
  goto :fail
)
if not exist "%VERIFY_ROOT%serve_windows.py" (
  set "FAIL_CODE=2"
  set "FAIL_REASON=serve_windows.py bulunamadi. Tam Windows projesi eksik."
  goto :fail
)
if not exist "%VERIFY_ROOT%launcher_windows.py" (
  set "FAIL_CODE=2"
  set "FAIL_REASON=launcher_windows.py bulunamadi. Tam Windows projesi eksik."
  goto :fail
)
if not exist "%VERIFY_ROOT%start_alpha.cmd" (
  set "FAIL_CODE=2"
  set "FAIL_REASON=start_alpha.cmd bulunamadi. Tam Windows projesi eksik."
  goto :fail
)
if not exist "%VERIFY_ROOT%tests\test_alpha20_revize_v2_compat.py" (
  set "FAIL_CODE=2"
  set "FAIL_REASON=V2 test dosyasi bulunamadi. ZIP'i yeniden proje kokune cikarin."
  goto :fail
)
echo [OK] Gerekli dosyalar bulundu.
>>"%VERIFY_LOG%" echo PREREQUISITES: PASS

echo.
echo [1/5] Governance preflight...
"%V2_PY%" "%VERIFY_ROOT%scripts\project_preflight.py" --check
if errorlevel 1 (
  set "FAIL_CODE=3"
  set "FAIL_REASON=Governance preflight basarisiz."
  goto :fail
)
>>"%VERIFY_LOG%" echo GOVERNANCE_PREFLIGHT: PASS

echo.
echo [2/5] Python derleme...
"%V2_PY%" -m compileall -q "%VERIFY_ROOT%app.py" "%VERIFY_ROOT%serve_windows.py" "%VERIFY_ROOT%launcher_windows.py" "%VERIFY_ROOT%portable_flock.py" "%VERIFY_ROOT%alpha20_v1"
if errorlevel 1 (
  set "FAIL_CODE=4"
  set "FAIL_REASON=Python derleme kontrolu basarisiz."
  goto :fail
)
>>"%VERIFY_LOG%" echo PYTHON_COMPILE: PASS

echo.
echo [3/5] Hedefli regresyon testleri...
"%V2_PY%" -m pytest -q "%VERIFY_ROOT%tests\test_alpha20_revize_v2_compat.py" "%VERIFY_ROOT%tests\test_dual_model.py" "%VERIFY_ROOT%tests\test_paper_trading.py" "%VERIFY_ROOT%tests\test_mission2400_windows_launcher.py" "%VERIFY_ROOT%tests\test_project_governance.py"
if errorlevel 1 (
  set "FAIL_CODE=5"
  set "FAIL_REASON=Hedefli regresyon testlerinden biri basarisiz."
  goto :fail
)
>>"%VERIFY_LOG%" echo TARGETED_TESTS: PASS

echo.
echo [4/5] Gercek Windows baslaticisi ve HTTP kontrolu...
call "%VERIFY_ROOT%start_alpha.cmd"
if errorlevel 1 (
  set "FAIL_CODE=6"
  set "FAIL_REASON=start_alpha.cmd uygulamayi baslatamadi. runtime\launcher.log dosyasina bakin."
  goto :fail
)
"%V2_PY%" -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=15); print('HEALTH_HTTP='+str(r.status)); raise SystemExit(0 if r.status==200 else 1)"
if errorlevel 1 (
  set "FAIL_CODE=7"
  set "FAIL_REASON=http://127.0.0.1:5000/health yanit vermedi."
  goto :fail
)
"%V2_PY%" -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:5000/home', timeout=15); print('HOME_HTTP='+str(r.status)); raise SystemExit(0 if r.status==200 else 1)"
if errorlevel 1 (
  set "FAIL_CODE=8"
  set "FAIL_REASON=/home acilamadi veya 404 dondu."
  goto :fail
)
>>"%VERIFY_LOG%" echo WINDOWS_START_AND_HTTP: PASS

echo.
echo [5/5] PAPER ve LIVE kilidi...
"%V2_PY%" -c "import sys;sys.path.insert(0,'alpha20_v1');import dual_model as d;s=d.snapshot();print('LIVE_ORDERS='+str(s.get('live_orders')));raise SystemExit(0 if s.get('live_orders')=='DISABLED' else 1)"
if errorlevel 1 (
  set "FAIL_CODE=9"
  set "FAIL_REASON=LIVE_ORDERS kilidi DISABLED degil."
  goto :fail
)
"%V2_PY%" -c "import json;p=json.load(open('governance/project_state.json',encoding='utf-8'));s=p['safety'];print('PAPER_ONLY='+str(s['paper_only']));print('EXCHANGE_WRITE_REQUESTS='+str(s['exchange_write_requests_allowed']));raise SystemExit(0 if s['paper_only'] and s['exchange_write_requests_allowed']==0 else 1)"
if errorlevel 1 (
  set "FAIL_CODE=10"
  set "FAIL_REASON=PAPER_ONLY veya sifir borsa yazma kilidi dogrulanamadi."
  goto :fail
)
>>"%VERIFY_LOG%" echo PAPER_AND_LIVE_LOCK: PASS

echo.
echo ============================================================
echo [PASS] Alpha20 Revize V2 Windows dogrulamasi tamamlandi.
echo Uygulama: http://127.0.0.1:5000/home
echo Log: %VERIFY_LOG%
echo ============================================================
>>"%VERIFY_LOG%" echo RESULT: PASS
echo.
pause
endlocal & exit /b 0

:fail
echo.
echo ============================================================
echo [FAIL] %FAIL_REASON%
echo Cikis kodu: %FAIL_CODE%
echo Log: %VERIFY_LOG%
echo ============================================================
>>"%VERIFY_LOG%" echo RESULT: FAIL
>>"%VERIFY_LOG%" echo EXIT_CODE: %FAIL_CODE%
>>"%VERIFY_LOG%" echo REASON: %FAIL_REASON%
echo.
echo Bu pencere kapanmayacak. Yukaridaki hata mesajini ekran goruntusuyle paylasin.
pause
endlocal & exit /b %FAIL_CODE%
