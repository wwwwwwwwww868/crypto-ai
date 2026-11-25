@echo off
rem Builds ChessAnalysis.exe into the dist folder (run on Windows).
cd /d "%~dp0"
py -m pip install --upgrade pywebview pyinstaller pillow || goto :err
py make_icon.py
py -m PyInstaller --noconfirm --onefile --windowed --name ChessAnalysis --icon icon.ico ^
  --add-data "index.html;." --add-data "chess.js;." --add-data "stockfish.asm.js;." ^
  --add-data "LICENSE-stockfish.txt;." --add-data "LICENSE-chess.js.txt;." main.py || goto :err
echo.
echo Done: dist\ChessAnalysis.exe
pause
exit /b 0
:err
echo Build failed - see the messages above.
pause
exit /b 1
