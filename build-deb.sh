#!/bin/bash
# Build a .deb package for Tonal
# Usage: ./build-deb.sh
set -e

VERSION="1.0.2"
PKG_NAME="tonal"
PKG_DIR="build/${PKG_NAME}_${VERSION}_all"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building ${PKG_NAME} ${VERSION}..."

# Clean previous build
rm -rf build/
mkdir -p "${PKG_DIR}"

# ── DEBIAN control files ────────────────────────────────────────────
mkdir -p "${PKG_DIR}/DEBIAN"

cat > "${PKG_DIR}/DEBIAN/control" << EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: all
Depends: python3 (>= 3.12), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, pipewire, pipewire-pulse, wireplumber
Maintainer: Thomas Hunt <hunt41lb@github.com>
Homepage: https://github.com/hunt41lb/tonal
Description: Audio routing and EQ manager for RODECaster Pro II
 Tonal is a GTK4/libadwaita desktop application that manages
 PipeWire audio routing and parametric EQ for the RODECaster
 Pro II on Linux. It creates per-channel filter chain sinks,
 provides a full parametric equalizer with profile management,
 and routes applications to specific audio channels automatically.
EOF

# Post-install: update icon cache and desktop database
cat > "${PKG_DIR}/DEBIAN/postinst" << 'EOF'
#!/bin/bash
set -e
if command -v gtk-update-icon-cache > /dev/null 2>&1; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
fi
if command -v update-desktop-database > /dev/null 2>&1; then
    update-desktop-database /usr/share/applications/ 2>/dev/null || true
fi
EOF
chmod 755 "${PKG_DIR}/DEBIAN/postinst"

# Post-remove: clean up icon cache
cat > "${PKG_DIR}/DEBIAN/postrm" << 'EOF'
#!/bin/bash
set -e
if command -v gtk-update-icon-cache > /dev/null 2>&1; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
fi
if command -v update-desktop-database > /dev/null 2>&1; then
    update-desktop-database /usr/share/applications/ 2>/dev/null || true
fi
EOF
chmod 755 "${PKG_DIR}/DEBIAN/postrm"

# ── Application files ───────────────────────────────────────────────
APP_DIR="${PKG_DIR}/usr/share/tonal"
mkdir -p "${APP_DIR}/pages"
mkdir -p "${APP_DIR}/widgets"
mkdir -p "${APP_DIR}/data"

# Core modules
cp "${SCRIPT_DIR}/tonal.py"            "${APP_DIR}/"
cp "${SCRIPT_DIR}/constants.py"        "${APP_DIR}/"
cp "${SCRIPT_DIR}/state.py"            "${APP_DIR}/"
cp "${SCRIPT_DIR}/config_gen.py"       "${APP_DIR}/"
cp "${SCRIPT_DIR}/pipewire_ctl.py"     "${APP_DIR}/"
cp "${SCRIPT_DIR}/eq_math.py"          "${APP_DIR}/"
cp "${SCRIPT_DIR}/eq_import_export.py" "${APP_DIR}/"
cp "${SCRIPT_DIR}/vu_meter.py"         "${APP_DIR}/"

# Pages
cp "${SCRIPT_DIR}/pages/__init__.py"   "${APP_DIR}/pages/"
cp "${SCRIPT_DIR}/pages/channels.py"   "${APP_DIR}/pages/"
cp "${SCRIPT_DIR}/pages/equalizer.py"  "${APP_DIR}/pages/"
cp "${SCRIPT_DIR}/pages/routing.py"    "${APP_DIR}/pages/"
cp "${SCRIPT_DIR}/pages/status.py"     "${APP_DIR}/pages/"

# Widgets
cp "${SCRIPT_DIR}/widgets/__init__.py"    "${APP_DIR}/widgets/"
cp "${SCRIPT_DIR}/widgets/eq_sliders.py"  "${APP_DIR}/widgets/"
cp "${SCRIPT_DIR}/widgets/helpers.py"     "${APP_DIR}/widgets/"

# Data (stylesheet)
cp "${SCRIPT_DIR}/data/style.css"      "${APP_DIR}/data/"

# ── Launcher ────────────────────────────────────────────────────────
mkdir -p "${PKG_DIR}/usr/bin"
cp "${SCRIPT_DIR}/bin/tonal" "${PKG_DIR}/usr/bin/tonal"
chmod 755 "${PKG_DIR}/usr/bin/tonal"

# ── Desktop entry ──────────────────────────────────────────────────
mkdir -p "${PKG_DIR}/usr/share/applications"
cp "${SCRIPT_DIR}/data/tonal.desktop" "${PKG_DIR}/usr/share/applications/com.tonal.app.desktop"

# ── Icons ───────────────────────────────────────────────────────────
# App icon → system hicolor theme (for dock/launcher)
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps"
cp "${SCRIPT_DIR}/data/icons/com.tonal.app.svg" \
   "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps/"

# Custom action icons → app data dir (for in-app toolbar buttons)
mkdir -p "${APP_DIR}/data/icons/scalable/actions"
cp "${SCRIPT_DIR}"/data/icons/scalable/actions/*.svg \
   "${APP_DIR}/data/icons/scalable/actions/"

# ── Build the .deb ──────────────────────────────────────────────────
dpkg-deb --build "${PKG_DIR}"

DEB_FILE="build/${PKG_NAME}_${VERSION}_all.deb"
echo ""
echo "✅ Package built: ${DEB_FILE}"
echo ""
echo "Install with:"
echo "  sudo dpkg -i ${DEB_FILE}"
echo ""
echo "If dependencies are missing, run:"
echo "  sudo apt install -f"
echo ""
echo "Uninstall with:"
echo "  sudo dpkg -r tonal"
