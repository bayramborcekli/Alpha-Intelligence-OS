@echo off
rem ============================================================
rem  Alpha Intelligence OS - Durdur (Mission 2400 Agent 01)
rem  Yalnizca baslaticinin kaydettigi PID'i durdurur; ilgisiz
rem  python sureclerine ASLA dokunmaz.
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows\stop_alpha.ps1"
