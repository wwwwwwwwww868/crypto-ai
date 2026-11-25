@echo off
cd /d "%~dp0"
echo Installing Python packages...
py -m pip install -r requirements.txt || python -m pip install -r requirements.txt
echo.
where tesseract >/dev/null 2>/dev/null || if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  echo Installing Tesseract OCR...
  winget install -e --id UB-Mannheim.TesseractOCR
)
echo.
echo Done. Double-click start.bat to run screen-agent.
pause
