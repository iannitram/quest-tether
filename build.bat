@echo off
echo === Quest Tether Build ===
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

REM Install dependencies
echo Installing dependencies...
pip install customtkinter pystray Pillow pyinstaller -q

REM Build EXE
echo.
echo Building QuestTether.exe...
pyinstaller build.spec --clean --noconfirm

echo.
if exist "dist\QuestTether.exe" (
    echo SUCCESS: dist\QuestTether.exe created!
    echo.
    echo Next steps:
    echo   1. Copy the tools\ folder into the same directory as QuestTether.exe
    echo   2. Make sure tools\ contains: gnirehtet.exe, gnirehtet.apk, adb.exe
    echo   3. Run QuestTether.exe
) else (
    echo BUILD FAILED. Check the output above for errors.
)
echo.
pause
