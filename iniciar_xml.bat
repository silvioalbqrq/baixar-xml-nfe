@echo off
setlocal
REM iniciar_xml.bat - SITE 100% LOCAL. Duplo-clique: liga e abre http://127.0.0.1:8002/
cd /d "%~dp0"
set PORTA=8002
set URL=http://127.0.0.1:%PORTA%/
echo ============================================
echo  Baixar XML de NFe/CT-e — SITE LOCAL
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python nao encontrado. Instale o Python 3 e marque Add to PATH no instalador.
  pause
  exit /b 1
)
echo [1/3] Verificando dependencias...
python -c "import fastapi, uvicorn, selenium, webdriver_manager, undetected_chromedriver" >nul 2>nul
if errorlevel 1 (
  echo Instalando dependencias locais...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERRO] Falha ao instalar. Verifique a internet e tente de novo.
    pause
    exit /b 1
  )
  python -c "import fastapi, uvicorn, selenium, webdriver_manager, undetected_chromedriver" >nul 2>nul
  if errorlevel 1 (
    echo [ERRO] Dependencias ainda faltando. Rode: python -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)
echo [2/3] Verificando se o backend ja esta no ar...
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%PORTA%/api/status', timeout=3).read()" >nul 2>nul
if not errorlevel 1 (
  echo Backend local ja estava no ar.
  goto abrir
)
echo [3/3] Ligando o backend local (janela "XML backend local")...
del /q backend.log >nul 2>nul
start "XML backend local" cmd /k "python backend.py > backend.log 2>&1 & echo. & echo === backend encerrado — log acima (e em backend.log) === & type backend.log"
echo Aguardando o backend responder (ate 30s)...
set TENT=0
:espera
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%PORTA%/api/status', timeout=3).read()" >nul 2>nul
if not errorlevel 1 (
  echo Backend local no ar!
  goto abrir
)
set /a TENT+=1
if %TENT% GEQ 30 (
  echo.
  echo [ERRO] Backend nao respondeu em 30s.
  echo --- log do backend: backend.log ---
  if exist backend.log ( type backend.log ) else ( echo sem log: o backend nem chegou a iniciar )
  echo --- fim do log ---
  echo.
  echo Causas comuns:
  echo  - Porta %PORTA% em uso: feche outra janela "XML backend local" e tente de novo.
  echo  - Antivirus bloqueando o Python.
  echo.
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
goto espera
:abrir
echo.
echo Abrindo o navegador padrao em %URL% ...
start "" "%URL%"
if errorlevel 1 (
  echo Metodo 1 falhou, tentando metodo alternativo...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process '%URL%'"
)
echo.
echo Se o navegador nao abriu sozinho, abra manualmente: %URL%
echo NAO feche esta janela nem a "XML backend local" enquanto usar.
echo DICA 1: na 1a vez marque "Sou humano" no Chrome que o robo abrir.
echo DICA 2: libere pop-ups no Chrome.
echo.
pause
