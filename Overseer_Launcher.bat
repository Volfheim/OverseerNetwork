@echo off
call "%~dp0overseer.cmd" open --notify
exit /b %errorlevel%
