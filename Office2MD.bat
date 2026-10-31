@echo off
title Office2MD
cd /d "%~dp0"
echo Starting Office2MD at http://localhost:8501 ...
echo (Keep this window open. Close it to stop the app.)
rem "py" is the Python launcher; fall back to "python" if it isn't installed
where py >nul 2>nul
if %errorlevel%==0 (py -m streamlit run app.py) else (python -m streamlit run app.py)
pause
