#!/usr/bin/env bash
set -e

# ── Luminary — Debian package build script ──────────────────────────
# Run this AFTER build-linux.sh has produced app/build/linux/portable/Luminary.
# Run from the project root: scripts/linux/build-deb.sh [version] [arch]
#
# USER DATA SAFETY: this script and the package it produces never touch
# ~/.local/share/Luminary (per-user data — DB, config, logs, thumbnails,
# cache). Every file this package installs lives under /opt/luminary,
# /usr/bin, or /usr/share — all replaceable application files. See
# scripts/linux/debian/prerm for why postrm/purge deliberately doesn't
# attempt to remove per-user data either.

VERSION="${1:-1.0.0}"
ARCH="${2:-amd64}"

SRC_DIR="app/build/linux/portable/Luminary"
PKG_ROOT="app/build/linux/deb-pkg"
DEB_OUT="app/build/linux/luminary_${VERSION}_${ARCH}.deb"
SCRIPTS_DIR="scripts/linux"

if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: $SRC_DIR not found — run build-linux.sh first." >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "ERROR: dpkg-deb not found. Install it with: sudo apt install dpkg-dev" >&2
    exit 1
fi

echo
echo "=== Assembling package tree ($PKG_ROOT) ==="
rm -rf "$PKG_ROOT"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/opt/luminary"
mkdir -p "$PKG_ROOT/usr/bin"
mkdir -p "$PKG_ROOT/usr/share/applications"
mkdir -p "$PKG_ROOT/usr/share/icons/hicolor/256x256/apps"

echo
echo "=== Copying built app into /opt/luminary ==="
# The whole onedir output — Luminary, _internal/, resources/, ffmpeg/ffprobe
# and run-luminary.sh if bundled — goes under /opt, the standard FHS location
# for self-contained third-party applications with their own bundled runtime.
cp -r "$SRC_DIR/." "$PKG_ROOT/opt/luminary/"

echo
echo "=== Installing launcher, desktop entry, icon ==="
cp "$SCRIPTS_DIR/usr-bin-luminary" "$PKG_ROOT/usr/bin/luminary"
cp "$SCRIPTS_DIR/luminary.desktop" "$PKG_ROOT/usr/share/applications/luminary.desktop"
if [ -f "$SCRIPTS_DIR/luminary.png" ]; then
    cp "$SCRIPTS_DIR/luminary.png" "$PKG_ROOT/usr/share/icons/hicolor/256x256/apps/luminary.png"
else
    echo "  (no $SCRIPTS_DIR/luminary.png found — skipping icon; add a 256x256 PNG there to enable it)"
fi

echo
echo "=== Writing DEBIAN/control (version $VERSION, arch $ARCH) ==="
sed -e "s/@VERSION@/$VERSION/" -e "s/@ARCH@/$ARCH/" \
    "$SCRIPTS_DIR/debian/control" > "$PKG_ROOT/DEBIAN/control"
cp "$SCRIPTS_DIR/debian/postinst" "$PKG_ROOT/DEBIAN/postinst"
cp "$SCRIPTS_DIR/debian/prerm" "$PKG_ROOT/DEBIAN/prerm"

echo
echo "=== Setting permissions ==="
find "$PKG_ROOT" -type d -exec chmod 755 {} \;
chmod 755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/prerm"
chmod 755 "$PKG_ROOT/usr/bin/luminary"
[ -f "$PKG_ROOT/opt/luminary/Luminary" ]        && chmod 755 "$PKG_ROOT/opt/luminary/Luminary"
[ -f "$PKG_ROOT/opt/luminary/run-luminary.sh" ] && chmod 755 "$PKG_ROOT/opt/luminary/run-luminary.sh"
[ -f "$PKG_ROOT/opt/luminary/ffmpeg" ]          && chmod 755 "$PKG_ROOT/opt/luminary/ffmpeg" "$PKG_ROOT/opt/luminary/ffprobe"

echo
echo "=== Building .deb ==="
dpkg-deb --build --root-owner-group "$PKG_ROOT" "$DEB_OUT"

echo
echo "Package built: $DEB_OUT"
echo "Install with:  sudo apt install ./$DEB_OUT"
echo "  (or:         sudo dpkg -i $DEB_OUT)"