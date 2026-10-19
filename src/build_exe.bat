@echo off
rem Builds dist\PathRenamer.exe (one-file, windowed, with the app icon).
rem Reconstructed for v1.2.0 from the shipped EXE: one-file build, GUI
rem subsystem, newicon.ico used as the EXE icon and bundled next to main.py.
cd /d "%~dp0"
echo Working directory: %CD%
echo ============================================
echo  PathRenamer - Build EXE
echo ============================================

echo Installing dependencies...
pip install -r requirements.txt
pip install pyinstaller

echo Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist PathRenamer.spec del PathRenamer.spec

echo Building EXE...
python -m PyInstaller --onefile --windowed --name PathRenamer --icon newicon.ico --add-data "newicon.ico;." --collect-all guessit --collect-all rebulk --collect-all babelfish --hidden-import rapidfuzz --hidden-import rapidfuzz.fuzz main.py

echo.
echo ============================================
echo  Done! EXE is in the dist\ folder.
echo ============================================
pause
