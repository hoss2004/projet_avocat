@echo off
cd /d "%~dp0"
docker compose up -d
if errorlevel 1 (
  echo.
  echo Demarrez Docker Desktop, puis relancez ce fichier.
  pause
  exit /b 1
)
echo.
echo Gardez Ollama ouvert pour les reponses du chatbot.
echo Connexion : valeur API_KEY du fichier .env, sans le prefixe API_KEY=.
start "" "http://localhost:3000"
