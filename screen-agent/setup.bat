@echo off
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
%PY% --version || (echo Python not found. Install it from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
%PY% -m pip install --upgrade pip
for %%p in (mss pyautogui pillow numpy opencv-python pytesseract) do (
  %PY% -m pip install %%p || echo *** FAILED to install %%p ***
)
where tesseract >nul 2>nul || if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  echo Installing Tesseract OCR...
  winget install -e --id UB-Mannheim.TesseractOCR
)
echo.
echo Done. Any "FAILED" lines above need fixing - send them to me.
echo Double-click start.bat to run screen-agent.
pause
