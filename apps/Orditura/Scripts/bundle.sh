#!/usr/bin/env bash
#
# Assemble Orditura.app from the SwiftPM build product.
#
# SwiftPM produces a bare executable; macOS needs a bundle for the app to have a
# stable identity. That identity is what a Full Disk Access grant attaches to,
# so running the raw executable would mean re-granting access every time the
# binary moves. Hence this script rather than `swift run`.
#
# For your own machine an ad-hoc signature is enough. For distribution the app
# must be Developer ID-signed and notarised — and it must stay non-sandboxed,
# because an App-Sandboxed process can never hold Full Disk Access, which is why
# this cannot ship through the Mac App Store. See docs/proposals/0001 §2.2.

set -euo pipefail

CONFIGURATION="${1:-release}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/.build/Orditura.app"

cd "$ROOT"
echo "Building ($CONFIGURATION)…"
swift build -c "$CONFIGURATION"

BIN="$(swift build -c "$CONFIGURATION" --show-bin-path)/Orditura"
if [ ! -x "$BIN" ]; then
    echo "error: no executable at $BIN" >&2
    exit 1
fi

echo "Assembling $APP…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Orditura"
cp "$ROOT/Resources/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

echo "Signing (ad-hoc)…"
codesign --force --sign - --timestamp=none "$APP"

cat <<NOTE

Built $APP

Next:
  1. open "$(dirname "$APP")"           and drag Orditura.app somewhere permanent
  2. System Settings > Privacy & Security > Full Disk Access > add Orditura
  3. Launch it. The grant is read at launch, so add it before first run.

Moving the app after granting access invalidates the grant; put it where you
want it first.
NOTE
