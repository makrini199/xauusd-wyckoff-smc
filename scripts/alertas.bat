@echo off
REM Doble clic para arrancar las alertas (Windows + MetaTrader 5 abierto).
REM Cambia 10000 por tu capital.
cd /d "%~dp0.."
python -m xauusd alertas --fuente mt5 --capital 10000 %*
pause
