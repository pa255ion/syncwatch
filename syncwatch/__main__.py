import argparse
import locale
import os
import sys


def _log_to_file() -> None:
    """Windowed (frozen) builds have no console, so errors would vanish.
    Send stdout/stderr, Qt messages and native crash tracebacks to a file."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
    path = os.path.join(base, "SyncWatch", "syncwatch.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        f = open(path, "w", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        return
    sys.stdout = sys.stderr = f
    if os.name != "nt":  # on Windows it also reports exceptions that get handled
        import faulthandler
        faulthandler.enable(f)
    from PySide6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(lambda _mode, _ctx, msg: print("qt:", msg, file=f))


def main() -> int:
    ap = argparse.ArgumentParser(prog="syncwatch", description="Watch videos in sync, peer to peer.")
    ap.add_argument("--port", type=int, default=47801, help="TCP port to listen on (default 47801)")
    ap.add_argument("--profile", default="", help="separate settings profile (e.g. to run two instances)")
    args = ap.parse_args()

    # libmpv embeds into a native X11 window, and Wayland doesn't let apps keep
    # themselves on top, so run through XWayland on Wayland sessions.
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") \
            and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    if sys.stderr is None or getattr(sys, "frozen", False):
        _log_to_file()

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("SyncWatch")
    locale.setlocale(locale.LC_NUMERIC, "C")  # libmpv requires this; Qt resets it

    try:
        from .gui import MainWindow
    except OSError as e:  # libmpv not found
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "SyncWatch", f"Could not load libmpv:\n{e}\n\nSee README.md for install steps.")
        return 1
    win = MainWindow(profile=args.profile, port=args.port)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
