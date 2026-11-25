@echo off
title QUANTUM TRADING SUITE - LAUNCHER COMPLETO
echo ======================================================================
echo           QUANTUM TRADING SUITE -- INICIADOR MAESTRO 24/7
echo ======================================================================
echo.

echo [1/2] Iniciando Dashboard Web en http://localhost:8000 ...
start "Quantum Dashboard (Puerto 8000)" cmd /k "cd /d %~dp0 && python dashboard_app.py --port 8000 --runtime-mode demo"

timeout /t 2 /nobreak >nul

echo [2/2] Iniciando Bots Cuantitativos MT5 (Oro, BTC, ETH, SOL + Telegram) ...
start "Quantum Bots MT5 (Supervisado)" cmd /k "cd /d %~dp0 && python run_demo_bots.py"

echo.
echo ======================================================================
echo  [OK] Todos los bots y paneles estan en ejecucion en ventanas separadas.
echo  - Dashboard Quantum: http://localhost:8000
echo ======================================================================
pause
