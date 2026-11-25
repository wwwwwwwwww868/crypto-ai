@echo off
cd /d "%~dp0"
py screen_agent.py %* || python screen_agent.py %*
pause
