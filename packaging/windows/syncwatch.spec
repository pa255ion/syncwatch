# PyInstaller spec for the Windows bundle; run via packaging/windows/build.sh.
import os

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
bins = os.path.join(root, "build", "windows", "bin")

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[root],
    binaries=[(os.path.join(bins, "libmpv-2.dll"), ".")],
    # helper programs mpv runs; data so PyInstaller doesn't dependency-scan them
    datas=[(os.path.join(bins, f), ".") for f in ("yt-dlp.exe", "deno.exe")],
    hiddenimports=["mpv"],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDBus"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SyncWatch",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="SyncWatch", upx=False)
