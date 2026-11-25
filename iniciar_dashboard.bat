@echo off
title QUANTUM DASHBOARD WEB
echo Iniciando Dashboard Web en http://localhost:8000 ...
cd /d %~dp0
python dashboard_app.py --port 8000 --runtime-mode demo
pause
