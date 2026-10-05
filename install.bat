@echo off
rem Double-click to install the Job Search Command Center as a background app.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" %*
pause
