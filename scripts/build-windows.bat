@echo off
setlocal

rem ── Luminary — Windows build script ──────────────────────────────
rem Run this from the project root (the folder containing app\, requirements.txt, run.sh)

set VENV_DIR=venv-win
set DIST_DIR=app\build\windows\portable

echo.
echo === Creating virtual environment (%VENV_DIR%) ===
if exist "%VENV_DIR%" (
    echo Virtual environment already exists, skipping creation.
) else (
    python -m venv %VENV_DIR%
    if errorlevel 1 goto :error
)

echo.
echo === Activating virtual environment ===
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 goto :error

echo.
echo === Installing dependencies ===
pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo === Cleaning previous build output ===
if exist "%DIST_DIR%" (
    rmdir /s /q "%DIST_DIR%"
)

echo.
echo === Building application with PyInstaller ===
pyinstaller --clean --onedir ^
    --add-data "app\src\frontend\index.html;." ^
    --add-data "app\src\frontend\static;static" ^
    --distpath "%DIST_DIR%" ^
    --name "Luminary" ^
    app\src\backend\app.py
if errorlevel 1 goto :error

echo.
echo === Deactivating virtual environment ===
call deactivate

echo.
echo Build complete: %DIST_DIR%\Luminary
goto :eof

:error
echo.
echo Build failed. See errors above.
call deactivate 2>nul
exit /b 1