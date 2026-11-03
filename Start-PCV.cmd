@echo off
powershell.exe -NoProfile -File "%~dp0Start-PCV.ps1"
if errorlevel 1 pause
