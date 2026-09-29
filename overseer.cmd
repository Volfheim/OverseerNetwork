@echo off
"%~dp0venv\Scripts\python.exe" "%~dp0scripts\panel.py" %*
exit /b %errorlevel%
