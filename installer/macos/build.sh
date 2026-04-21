#!/bin/bash
set -euo pipefail

VERSION="${1:?Usage: build.sh VERSION}"
ARCH=$(uname -m)

echo "Building claude-standup $VERSION for macOS ($ARCH)..."

pip install pyinstaller
pyinstaller claude-standup.spec --clean --noconfirm

STAGING=$(mktemp -d)
mkdir -p "$STAGING/usr/local/bin"
cp dist/claude-standup "$STAGING/usr/local/bin/"

pkgbuild \
    --root "$STAGING" \
    --identifier com.theburrowhub.claude-standup \
    --version "$VERSION" \
    --scripts installer/macos \
    "dist/claude-standup-${VERSION}-macos-${ARCH}.pkg"

echo "Built: dist/claude-standup-${VERSION}-macos-${ARCH}.pkg"
