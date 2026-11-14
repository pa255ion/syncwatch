#!/usr/bin/env bash
# Build dist/SyncWatch-windows-x64.zip from Linux using Wine.
# Needs: wine (64-bit), a MinGW gcc, curl, 7z, unzip, zip. With Nix:
#   nix shell nixpkgs#wineWow64Packages.stable nixpkgs#pkgsCross.mingwW64.buildPackages.gcc \
#     nixpkgs#p7zip nixpkgs#unzip nixpkgs#zip nixpkgs#curl nixpkgs#xvfb-run \
#     -c xvfb-run -a packaging/windows/build.sh
# Downloads are cached in build/windows/dl; REFRESH=1 re-fetches them.
set -euo pipefail
cd "$(dirname "$0")/../.."
out=build/windows
mkdir -p "$out/dl" "$out/bin"
[ "${REFRESH:-0}" = 1 ] && rm -f "$out"/dl/* "$out"/bin/*
# no Mono/Gecko: their install prompt hangs a headless wineboot
export WINEPREFIX="$PWD/$out/wine" WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=d"
pyver=3.12.10

fetch() {  # url dest
  [ -s "$2" ] && return
  echo "fetching $1"
  curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"
}

# 1. Windows Python inside the Wine prefix
fetch "https://www.python.org/ftp/python/$pyver/python-$pyver-amd64.exe" "$out/dl/python.exe"
if [ ! -f "$WINEPREFIX/drive_c/Python312/python.exe" ]; then
  wineboot -i && wineserver -w
  wine "$out/dl/python.exe" /quiet InstallAllUsers=0 'TargetDir=C:\Python312' \
    Include_test=0 Include_doc=0 Include_tcltk=0 Include_launcher=0 \
    Shortcuts=0 AssociateFiles=0 PrependPath=0
fi
# Qt imports Windows' system icuuc.dll, which Wine lacks (see icuuc_stub.c)
sys32="$WINEPREFIX/drive_c/windows/system32"
if [ ! -f "$sys32/icuuc.dll" ]; then
  x86_64-w64-mingw32-gcc -O2 -shared -nostdlib -Wl,-e,DllEntry -o "$sys32/icuuc.dll" \
    packaging/windows/icuuc_stub.c
fi
py() { wine 'C:\Python312\python.exe' "$@"; }
py -m pip install -q --disable-pip-version-check --upgrade \
  pyinstaller PySide6-Essentials python-mpv yt-dlp

# 2. Programs bundled next to the app
latest_asset() {  # repo regex
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" \
    | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(http[^"]*\)"/\1/' \
    | grep -E "$2" | head -1
}
fetch "$(latest_asset shinchiro/mpv-winbuild-cmake '/mpv-dev-x86_64-[0-9][^/]*\.7z$')" "$out/dl/mpv-dev.7z"
[ -s "$out/bin/libmpv-2.dll" ] || 7z e -y -o"$out/bin" "$out/dl/mpv-dev.7z" libmpv-2.dll >/dev/null
fetch https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe "$out/bin/yt-dlp.exe"
fetch https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip "$out/dl/deno.zip"
[ -s "$out/bin/deno.exe" ] || unzip -o -q -d "$out/bin" "$out/dl/deno.zip" deno.exe

# 3. Freeze and zip
rm -rf "$out/dist"
py -m PyInstaller --noconfirm --log-level WARN --distpath "$out/dist" \
  --workpath "$out/work" packaging/windows/syncwatch.spec
for f in platforms/qwindows.dll; do  # Qt hook silently skips these if Qt can't load
  [ -f "$out/dist/SyncWatch/_internal/PySide6/plugins/$f" ] || { echo "missing Qt plugin $f" >&2; exit 1; }
done
if find "$out/dist" -iname 'icuuc.dll' | grep -q .; then
  echo "build stub icuuc.dll leaked into the bundle" >&2; exit 1
fi
cp README.md "$out/dist/SyncWatch/README.md"
rm -f "$out/SyncWatch-windows-x64.zip"
(cd "$out/dist" && zip -qr ../SyncWatch-windows-x64.zip SyncWatch)
ls -lh "$out/SyncWatch-windows-x64.zip"
