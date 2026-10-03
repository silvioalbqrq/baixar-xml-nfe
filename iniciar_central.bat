@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [Central] Instalando dependencias...
python -m pip install -r requirements_central.txt
echo [Central] Ligando backend em http://127.0.0.1:8004/
start "" "http://127.0.0.1:8004/"
python -m uvicorn backend_central:app --host 127.0.0.1 --port 8004
pause
