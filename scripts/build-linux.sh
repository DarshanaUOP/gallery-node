#!/usr/bin/env bash
set -e

# ── Luminary — Linux build script ──────────────────────────────────
# Run this from the project root (the folder containing app/, requirements.txt, run.sh)

PYTHON=${PYTHON:-python3}

VENV_DIR="venv-linux"
DIST_DIR="app/build/linux/portable"

echo
echo "=== Creating virtual environment ($VENV_DIR) ==="
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment already exists, skipping creation."
else
    if "$PYTHON" -c "import ensurepip" >/dev/null 2>&1; then
        "$PYTHON" -m venv "$VENV_DIR"
    else
        echo "ensurepip is not available for $PYTHON. Trying fallbacks..."
        if command -v virtualenv >/dev/null 2>&1; then
            virtualenv -p "$PYTHON" "$VENV_DIR"
        elif "$PYTHON" -m pip --version >/dev/null 2>&1; then
            echo "Installing virtualenv into the user site-packages..."
            "$PYTHON" -m pip install --user virtualenv
            PATH="$HOME/.local/bin:$PATH"
            if command -v virtualenv >/dev/null 2>&1; then
                virtualenv -p "$PYTHON" "$VENV_DIR"
            else
                echo "Failed to install virtualenv into user bin."
                echo "On Debian/Ubuntu you can enable venv support by running:"
                echo "  sudo apt install python3-venv"
                exit 1
            fi
        else
            echo "ensurepip and pip are not available for $PYTHON." >&2
            echo "On Debian/Ubuntu, install venv support with: sudo apt install python3-venv" >&2
            echo "Or install pip/virtualenv and re-run this script." >&2
            exit 1
        fi
    fi
fi

echo
echo "=== Activating virtual environment ==="
source "$VENV_DIR/bin/activate"

echo
echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo
echo "=== Cleaning previous build output ==="
rm -rf "$DIST_DIR"

echo
echo "=== Building application with PyInstaller ==="
# --hidden-import: pystray (the system tray icon — see app/src/backend/tray.py)
# picks whichever Linux backend is actually available (AppIndicator, GTK
# StatusIcon, or plain Xorg) at runtime via a try/except chain, which
# PyInstaller's static import scan can miss. Listing all three here is
# harmless if a given machine doesn't have the matching libraries — pystray
# still falls back gracefully at runtime, this just ensures whichever one
# *is* available got bundled.
pyinstaller --clean --onedir \
    --distpath "$DIST_DIR" \
    --name "Luminary" \
    --hidden-import "pystray._gtk" \
    --hidden-import "pystray._appindicator" \
    --hidden-import "pystray._xorg" \
    app/src/backend/app.py

echo
echo "=== Copying frontend (html/css/js) into the built app ==="
# app_paths.get_resources_dir() resolves bundled resources to <install_dir>/resources
# for frozen builds — a sibling of PyInstaller's own _internal/ folder, kept
# separate so it's clear which parts are our assets vs. PyInstaller's runtime
# bundle, and so user data (which now lives outside the install dir entirely)
# is never confused with either.
rm -rf "$DIST_DIR/Luminary/resources"
cp -r "app/src/frontend" "$DIST_DIR/Luminary/resources"

echo
echo "=== Deactivating virtual environment ==="
deactivate

echo
echo "=== Ensuring ffmpeg / ffprobe are available to bundle ==="
FFMPEG_CACHE=".ffmpeg-cache/linux"
FFMPEG_BUNDLED=0

if [ -f "$FFMPEG_CACHE/ffmpeg" ] && [ -f "$FFMPEG_CACHE/ffprobe" ]; then
    echo "Using cached static build from $FFMPEG_CACHE"
    FFMPEG_BUNDLED=1
else
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)         FFMPEG_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" ;;
        aarch64|arm64)  FFMPEG_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz" ;;
        *)              FFMPEG_URL="" ;;
    esac

    if [ -n "$FFMPEG_URL" ]; then
        echo "ffmpeg not cached yet — downloading a static build for $ARCH..."
        mkdir -p "$FFMPEG_CACHE"
        if curl -fL -o "$FFMPEG_CACHE/ffmpeg.tar.xz" "$FFMPEG_URL" 2>/dev/null \
           && tar -xf "$FFMPEG_CACHE/ffmpeg.tar.xz" -C "$FFMPEG_CACHE" --strip-components=1; then
            rm -f "$FFMPEG_CACHE/ffmpeg.tar.xz"
            chmod +x "$FFMPEG_CACHE/ffmpeg" "$FFMPEG_CACHE/ffprobe"
            echo "Downloaded and cached in $FFMPEG_CACHE (reused on future builds)."
            FFMPEG_BUNDLED=1
        else
            echo "Download failed (no internet access?) — will try a system install instead."
            rm -rf "$FFMPEG_CACHE"
        fi
    else
        echo "No static build available for architecture '$ARCH' — will try a system install instead."
    fi
fi

if [ "$FFMPEG_BUNDLED" = "1" ]; then
    cp "$FFMPEG_CACHE/ffmpeg" "$DIST_DIR/Luminary/ffmpeg"
    cp "$FFMPEG_CACHE/ffprobe" "$DIST_DIR/Luminary/ffprobe"
    chmod +x "$DIST_DIR/Luminary/ffmpeg" "$DIST_DIR/Luminary/ffprobe"
else
    FFMPEG_PATH="$(command -v ffmpeg || true)"
    FFPROBE_PATH="$(command -v ffprobe || true)"
    if [ -n "$FFMPEG_PATH" ] && [ -n "$FFPROBE_PATH" ]; then
        echo "Found system ffmpeg: $FFMPEG_PATH"
        cp "$FFMPEG_PATH" "$DIST_DIR/Luminary/ffmpeg"
        cp "$FFPROBE_PATH" "$DIST_DIR/Luminary/ffprobe"
        chmod +x "$DIST_DIR/Luminary/ffmpeg" "$DIST_DIR/Luminary/ffprobe"
        FFMPEG_BUNDLED=1
    fi
fi

if [ "$FFMPEG_BUNDLED" = "1" ]; then
    # Unlike Windows, Linux does not search a binary's own folder for bare
    # command names like "ffmpeg" — app.py calls subprocess.run(["ffmpeg", ...])
    # relying on PATH, so a plain `./Luminary` would still miss the bundled
    # copy. Write a launcher that puts this folder on PATH first.
    cat > "$DIST_DIR/Luminary/run-luminary.sh" <<'EOF'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$DIR:$PATH"
exec "$DIR/Luminary" "$@"
EOF
    chmod +x "$DIST_DIR/Luminary/run-luminary.sh"
    echo "Bundled ffmpeg/ffprobe. Launch via run-luminary.sh so they're found."
else
    echo "Could not download or find ffmpeg/ffprobe — the app will still run,"
    echo "but video duration/resolution/fps/GPS metadata and video thumbnails"
    echo "will be limited. Re-run this script with internet access to bundle it."
fi

echo
echo "Build complete: $DIST_DIR/Luminary (resources at $DIST_DIR/Luminary/resources)"