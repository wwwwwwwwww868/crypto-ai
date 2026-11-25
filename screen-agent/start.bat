@echo off
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
%PY% --version >nul 2>nul || (echo Python not found. Install it from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)

%PY% -c "import mss, pyautogui, PIL, numpy, cv2, PySide6, chess" 2>nul && goto run
echo Installing missing Python packages (first run only, takes a minute)...
%PY% -m pip install --upgrade pip
for %%p in (mss pyautogui pillow numpy opencv-python pytesseract PySide6-Essentials chess) do (
  %PY% -m pip install %%p || echo *** FAILED to install %%p ***
)
%PY% -c "import mss, pyautogui, PIL, numpy, cv2, PySide6, chess" || (echo. & echo Install failed - copy the messages above and send them to me. & pause & exit /b 1)

:run
start "" %PY%w app.py 2>nul || %PY% app.py
exit /b 0
