@echo off
rem ============================================================
rem  Alpha Intelligence OS - Baslat (Mission 2400 Agent 01)
rem  Masaustu kisayolu bu dosyayi calistirir. Asil is PowerShell
rem  basaticisindadir; bu pencere hemen kucultulup kapanir.
rem ============================================================
start "Alpha Intelligence OS" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows\launch_alpha.ps1"
exit /b 0
