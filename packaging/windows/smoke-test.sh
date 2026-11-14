#!/usr/bin/env bash
# Run the built Windows bundle under Wine with a native Linux peer and check
# that they play in sync. Needs the build prefix from build.sh plus Xvfb,
# openbox, ImageMagick, unzip, MinGW gcc, and the Linux dev shell. With Nix:
#   nix shell nixpkgs#wineWow64Packages.stable nixpkgs#pkgsCross.mingwW64.buildPackages.gcc \
#     nixpkgs#xorg-server nixpkgs#openbox nixpkgs#imagemagick nixpkgs#unzip \
#     --inputs-from . -c nix develop -c packaging/windows/smoke-test.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
out=build/windows
smoke=$out/smoke
export WINEPREFIX="$PWD/$out/wine" WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=d;mscms=n"
rm -rf "$smoke" && mkdir -p "$smoke"
unzip -q "$out/SyncWatch-windows-x64.zip" -d "$smoke"
# test copy only: work around Wine's missing WcsCreateIccProfile
x86_64-w64-mingw32-gcc -O2 -shared -nostdlib -Wl,-e,DllEntry \
  -o "$smoke/SyncWatch/_internal/mscms.dll" packaging/windows/wine_mscms_stub.c

Xvfb :93 -screen 0 1920x1080x24 >/dev/null 2>&1 & xv=$!
sleep 1
export DISPLAY=:93
openbox >/dev/null 2>&1 & ob=$!
SYNCWATCH_MPV_OPTS="${SYNCWATCH_MPV_OPTS:-}" wine "$smoke/SyncWatch/SyncWatch.exe" > "$smoke/wine.log" 2>&1 & exe=$!
sleep 15
python packaging/windows/smoke-test.py > "$smoke/peer.log" 2>&1 & peer=$!
sleep 40 && import -window root "$smoke/screen.png"
wait $peer
kill -0 $exe 2>/dev/null && echo "exe: still running" || echo "exe: EXITED"
wineserver -k; kill $ob $xv 2>/dev/null
cat "$smoke/peer.log" | grep '^\['
echo "--- exe log"
find "$WINEPREFIX/drive_c/users" -name syncwatch.log -exec cat {} \;
