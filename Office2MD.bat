@echo off
title Office2MD
cd /d "%~dp0"
echo Starting Office2MD at http://localhost:8501 ...
echo (Keep this window open. Close it to stop the app.)
"%APPDATA%\Python\Python314\Scripts\streamlit.exe" run app.py
pause
