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
DEB_OUT="app/build/linux/installer/luminary_${VERSION}_${ARCH}.deb"
SCRIPTS_DIR="scripts/linux"
ICON_DIR="app/src/frontend/images"

if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: $SRC_DIR not found — run build-linux.sh first." >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "ERROR: dpkg-deb not found. Install it with: sudo apt install dpkg-dev" >&2
    exit 1
fi

# Stage the package tree under /tmp rather than inside the project checkout.
# dpkg-deb requires the DEBIAN control directory to be mode 0755-0775, and
# chmod can silently fail to stick on filesystems that don't support real
# Unix permission bits — most commonly a Windows-mounted path under WSL
# (/mnt/c/...), a network share, or an exFAT/NTFS drive. Building in /tmp
# sidesteps that regardless of where the project source itself lives.
WORK_ROOT="$(mktemp -d /tmp/luminary-deb-XXXXXX)"
trap 'rm -rf "$WORK_ROOT"' EXIT
PKG_ROOT="$WORK_ROOT/deb-pkg"

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
# sed strips any CRLF line endings unconditionally, regardless of how these
# source files were last saved. This matters specifically for scripts with a
# shebang line: "#!/bin/sh\r" makes the kernel look for an interpreter
# literally named "/bin/sh<CR>", which doesn't exist — dpkg then fails with
# a confusing "unable to execute ... No such file or directory" when it
# tries to run postinst/prerm during install or removal.
sed 's/\r$//' "$SCRIPTS_DIR/usr-bin-luminary" > "$PKG_ROOT/usr/bin/luminary"
cp "$SCRIPTS_DIR/luminary.desktop" "$PKG_ROOT/usr/share/applications/luminary.desktop"
if [ -f "$ICON_DIR/luminary.png" ]; then
    cp "$SCRIPTS_DIR/luminary.png" "$PKG_ROOT/usr/share/icons/hicolor/256x256/apps/luminary.png"
else
    echo "  (no $SCRIPTS_DIR/luminary.png found — skipping icon; add a 256x256 PNG there to enable it)"
fi

echo
echo "=== Writing DEBIAN/control (version $VERSION, arch $ARCH) ==="
sed -e "s/@VERSION@/$VERSION/" -e "s/@ARCH@/$ARCH/" -e 's/\r$//' \
    "$SCRIPTS_DIR/debian/control" > "$PKG_ROOT/DEBIAN/control"
sed 's/\r$//' "$SCRIPTS_DIR/debian/postinst" > "$PKG_ROOT/DEBIAN/postinst"
sed 's/\r$//' "$SCRIPTS_DIR/debian/prerm" > "$PKG_ROOT/DEBIAN/prerm"

echo
echo "=== Setting permissions ==="
find "$PKG_ROOT" -type d -exec chmod 755 {} \;
chmod 755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/prerm"
chmod 755 "$PKG_ROOT/usr/bin/luminary"
[ -f "$PKG_ROOT/opt/luminary/Luminary" ]        && chmod 755 "$PKG_ROOT/opt/luminary/Luminary"
[ -f "$PKG_ROOT/opt/luminary/run-luminary.sh" ] && chmod 755 "$PKG_ROOT/opt/luminary/run-luminary.sh"
[ -f "$PKG_ROOT/opt/luminary/ffmpeg" ]          && chmod 755 "$PKG_ROOT/opt/luminary/ffmpeg" "$PKG_ROOT/opt/luminary/ffprobe"

# Verify no CRLF slipped into any executable script — belt-and-suspenders on
# top of the sed stripping above, in case this script is ever edited to add
# another script source without going through the same sed step.
for f in "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/prerm" "$PKG_ROOT/usr/bin/luminary"; do
    if grep -qP '\r$' "$f" 2>/dev/null; then
        echo "ERROR: $f contains CRLF line endings — dpkg will fail to execute" >&2
        echo "  it (shebang becomes '#!/bin/sh<CR>', an interpreter that doesn't" >&2
        echo "  exist). Run: sed -i 's/\\r\$//' $f" >&2
        exit 1
    fi
done

# Verify the chmod actually stuck. If it didn't, even /tmp is behaving oddly
# (e.g. mounted with unusual options) — fail with a clear diagnosis instead
# of letting dpkg-deb's more cryptic error surface.
ACTUAL_MODE="$(stat -c '%a' "$PKG_ROOT/DEBIAN")"
if [ "$ACTUAL_MODE" != "755" ]; then
    echo "ERROR: $PKG_ROOT/DEBIAN is mode $ACTUAL_MODE after chmod 755 — this" >&2
    echo "  filesystem isn't honoring Unix permissions (seen with WSL /mnt/c" >&2
    echo "  paths, network shares, and exFAT/NTFS mounts). Try running this" >&2
    echo "  script with \$TMPDIR pointed at a native Linux filesystem, e.g.:" >&2
    echo "    TMPDIR=/var/tmp scripts/linux/build-deb.sh $VERSION $ARCH" >&2
    exit 1
fi

echo
echo "=== Building .deb ==="
dpkg-deb --build --root-owner-group "$PKG_ROOT" "$WORK_ROOT/luminary.deb"
mkdir -p "$(dirname "$DEB_OUT")"
cp "$WORK_ROOT/luminary.deb" "$DEB_OUT"

echo
echo "Package built: $DEB_OUT"
echo "Install with:  sudo apt install ./$DEB_OUT"
echo "  (or:         sudo dpkg -i $DEB_OUT)"