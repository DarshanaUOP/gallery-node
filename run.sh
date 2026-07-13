#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Luminary — Local Media Gallery  |  run.sh
#  Starts the backend API (Flask) and serves the frontend.
# ─────────────────────────────────────────────────────────────────────────────

set -e

PYTHON=${PYTHON:-python3}
PORT=${PORT:-5000}

# Force unbuffered stdout/stderr. Without this, Python fully buffers output
# whenever it's not attached to a real terminal (systemd, Docker, nohup, a
# log file redirect, etc.) — logs still get written, they just sit in a
# buffer and don't show up until it fills or the process exits.
export PYTHONUNBUFFERED=1

# ── dependency check ──────────────────────────────────────────────────────────
echo "▸ Checking dependencies…"

if ! $PYTHON -c "import flask" 2>/dev/null; then
  echo "  Installing Flask…"
  $PYTHON -m pip install flask flask-cors --quiet
fi

if ! $PYTHON -c "import PIL" 2>/dev/null; then
  echo "  Installing Pillow (EXIF support)…"
  $PYTHON -m pip install Pillow --quiet
fi

if ! $PYTHON -c "import waitress" 2>/dev/null; then
  echo "  Installing Waitress (production server)…"
  $PYTHON -m pip install waitress --quiet
fi

echo "  Dependencies OK."

# ── optional initial sync ─────────────────────────────────────────────────────
if [ "$1" == "--sync" ]; then
  echo "▸ Running initial sync…"
  $PYTHON app/src/backend/app.py --sync-only
fi

# ── start server ─────────────────────────────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Luminary  ·  Local Media Gallery   ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
echo "  → Open:  http://localhost:$PORT"
echo "  → API:   http://localhost:$PORT/api/media"
echo "  → Press  Ctrl+C  to stop"
echo ""

exec $PYTHON app/src/backend/app.py --port "$PORT"