@echo off
title Paper Trader - Momentum Ignition
cd /d "%~dp0"
echo Paper trader starting... keep this window open. Trades log to paper_trades.csv
python paper_trader.py
pause
