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
    --distpath "%DIST_DIR%" ^
    --name "Luminary" ^
    --icon "app\src\frontend\images\luminary.ico" ^
    app\src\backend\app.py
if errorlevel 1 goto :error

echo.
echo === Copying frontend (html/css/js) into the built app ===
rem app_paths.get_resources_dir() resolves bundled resources to <install_dir>\resources
rem for frozen builds — a sibling of PyInstaller's own _internal\ folder, kept
rem separate so it's clear which parts are our assets vs. PyInstaller's runtime
rem bundle, and so user data (which now lives outside the install dir entirely)
rem is never confused with either.
if exist "%DIST_DIR%\Luminary\resources" (
    rmdir /s /q "%DIST_DIR%\Luminary\resources"
)
xcopy /E /I /Y "app\src\frontend" "%DIST_DIR%\Luminary\resources" >nul
if errorlevel 1 goto :error

echo.
echo === Deactivating virtual environment ===
call deactivate

echo.
echo === Ensuring ffmpeg / ffprobe are available to bundle ===
set FFMPEG_CACHE=.ffmpeg-cache\windows
if exist "%FFMPEG_CACHE%\ffmpeg.exe" if exist "%FFMPEG_CACHE%\ffprobe.exe" goto :ffmpeg_cached

echo ffmpeg not cached yet — downloading a static build...
if not exist "%FFMPEG_CACHE%" mkdir "%FFMPEG_CACHE%"
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%FFMPEG_CACHE%\ffmpeg.zip' } catch { exit 1 }"
if errorlevel 1 goto :ffmpeg_download_failed

powershell -NoProfile -Command "Expand-Archive -Path '%FFMPEG_CACHE%\ffmpeg.zip' -DestinationPath '%FFMPEG_CACHE%\extracted' -Force"
if errorlevel 1 goto :ffmpeg_download_failed

for /d %%D in ("%FFMPEG_CACHE%\extracted\ffmpeg-*") do (
    copy /Y "%%D\bin\ffmpeg.exe" "%FFMPEG_CACHE%\ffmpeg.exe" >nul
    copy /Y "%%D\bin\ffprobe.exe" "%FFMPEG_CACHE%\ffprobe.exe" >nul
)
del "%FFMPEG_CACHE%\ffmpeg.zip" >nul 2>&1
rmdir /s /q "%FFMPEG_CACHE%\extracted" >nul 2>&1

if not exist "%FFMPEG_CACHE%\ffmpeg.exe" goto :ffmpeg_download_failed
if not exist "%FFMPEG_CACHE%\ffprobe.exe" goto :ffmpeg_download_failed
echo Downloaded and cached in %FFMPEG_CACHE% (reused on future builds).
goto :ffmpeg_cached

:ffmpeg_download_failed
echo Download failed (no internet access?) — falling back to a system install if present.
where ffmpeg >nul 2>&1
if errorlevel 1 goto :no_ffmpeg
where ffprobe >nul 2>&1
if errorlevel 1 goto :no_ffmpeg
for /f "delims=" %%F in ('where ffmpeg') do copy /Y "%%F" "%DIST_DIR%\Luminary\ffmpeg.exe" >nul
for /f "delims=" %%F in ('where ffprobe') do copy /Y "%%F" "%DIST_DIR%\Luminary\ffprobe.exe" >nul
echo Bundled ffmpeg/ffprobe from this machine's PATH.
goto :ffmpeg_done

:ffmpeg_cached
echo Bundling ffmpeg/ffprobe from %FFMPEG_CACHE% into the build...
copy /Y "%FFMPEG_CACHE%\ffmpeg.exe" "%DIST_DIR%\Luminary\ffmpeg.exe" >nul
copy /Y "%FFMPEG_CACHE%\ffprobe.exe" "%DIST_DIR%\Luminary\ffprobe.exe" >nul
goto :ffmpeg_done

:no_ffmpeg
echo Could not download or find ffmpeg/ffprobe — the app will still run, but
echo video duration/resolution/fps/GPS metadata and video thumbnails will be
echo limited. Re-run this script with internet access to bundle it.

:ffmpeg_done
echo.
echo Build complete: %DIST_DIR%\Luminary  (resources at %DIST_DIR%\Luminary\resources)
goto :eof

:error
echo.
echo Build failed. See errors above.
call deactivate 2>nul
exit /b 1