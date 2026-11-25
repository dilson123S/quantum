@echo off
title QUANTUM BOTS CRYPTO 24/7
echo Iniciando Bots Cripto (BTC H1, BTC H4, ETH H1, SOL H1 + Telegram)...
cd /d %~dp0
python run_demo_bots.py --crypto-only
pause
