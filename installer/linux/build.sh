#!/bin/bash
set -euo pipefail

VERSION="${1:?Usage: build.sh VERSION}"
ARCH="amd64"

echo "Building claude-standup $VERSION for Linux ($ARCH)..."

pip install pyinstaller
pyinstaller claude-standup.spec --clean --noconfirm

DEB_DIR=$(mktemp -d)
mkdir -p "$DEB_DIR/usr/local/bin"
mkdir -p "$DEB_DIR/usr/lib/systemd/user"
mkdir -p "$DEB_DIR/DEBIAN"

cp dist/claude-standup "$DEB_DIR/usr/local/bin/"
cp installer/linux/claude-standup.service "$DEB_DIR/usr/lib/systemd/user/"

cat > "$DEB_DIR/DEBIAN/control" << CTRL
Package: claude-standup
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: theburrowhub
Description: Daily standup reports from Claude Code activity logs
 Background daemon that continuously classifies development sessions
 and generates instant standup reports.
CTRL

cp installer/linux/postinst "$DEB_DIR/DEBIAN/"
cp installer/linux/prerm "$DEB_DIR/DEBIAN/"
chmod 755 "$DEB_DIR/DEBIAN/postinst" "$DEB_DIR/DEBIAN/prerm"

dpkg-deb --build "$DEB_DIR" "dist/claude-standup_${VERSION}_${ARCH}.deb"

echo "Built: dist/claude-standup_${VERSION}_${ARCH}.deb"
