#!/usr/bin/env bash
set -e

# ── Luminary — Linux build script ──────────────────────────────────
# Run this from the project root (the folder containing app/, requirements.txt, run.sh)

VENV_DIR="venv-linux"
DIST_DIR="app/build/linux/portable"

echo
echo "=== Creating virtual environment ($VENV_DIR) ==="
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment already exists, skipping creation."
else
    python3 -m venv "$VENV_DIR"
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
pyinstaller --clean --onedir \
    --distpath "$DIST_DIR" \
    --name "Luminary" \
    app/src/backend/app.py

echo
echo "=== Copying frontend (html/css/js) into the built app ==="
# app.py resolves frontend_dir as BASE_DIR.parent / "frontend" — frontend
# must live inside the "Luminary" output folder, alongside the Luminary binary.
rm -rf "$DIST_DIR/Luminary/frontend"
cp -r "app/src/frontend" "$DIST_DIR/Luminary/frontend"

echo
echo "=== Deactivating virtual environment ==="
deactivate

echo
echo "Build complete: $DIST_DIR/Luminary (frontend at $DIST_DIR/Luminary/frontend)"