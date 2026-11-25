@echo off
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
%PY% --version || (echo Python not found. Install it from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)

%PY% -c "import mss, pyautogui, PIL, numpy" 2>nul && goto run
echo Installing missing Python packages (first run only)...
%PY% -m pip install --upgrade pip
for %%p in (mss pyautogui pillow numpy opencv-python pytesseract) do (
  %PY% -m pip install %%p || echo *** FAILED to install %%p ***
)
%PY% -c "import mss, pyautogui, PIL, numpy" || (echo. & echo Install failed - copy the messages above and send them to me. & pause & exit /b 1)

:run
%PY% screen_agent.py %*
pause
