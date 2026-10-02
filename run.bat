@echo off
rem Starts Clara's desktop app without a console window (from this folder's virtual environment).
start "" "%~dp0.venv\Scripts\pythonw.exe" -m clara_app %*
