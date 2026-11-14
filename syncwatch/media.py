"""yt-dlp integration: title/playlist lookup and finding the executable
that mpv's built-in ytdl hook runs to resolve stream URLs."""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import sysconfig

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

MAX_PLAYLIST = 200


def bundle_dir() -> str | None:
    """Where a PyInstaller build keeps its bundled files (libmpv, yt-dlp, deno)."""
    return getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def ytdlp_path() -> str | None:
    """Prefer the bundled yt-dlp, then the one installed alongside this Python."""
    exe = _exe("yt-dlp")
    candidates = [
        os.path.join(sysconfig.get_path("scripts"), exe),
        os.path.join(os.path.dirname(sys.executable), exe),
        os.path.join(os.path.dirname(sys.executable), "Scripts", exe),
    ]
    if bundle_dir():
        candidates.insert(0, os.path.join(bundle_dir(), exe))
    for c in candidates:
        if os.path.isfile(c):
            return c
    return shutil.which("yt-dlp")


def bundled_deno() -> str | None:
    """The JS runtime yt-dlp needs for YouTube, if this build ships one."""
    d = bundle_dir()
    path = os.path.join(d, _exe("deno")) if d else None
    return path if path and os.path.isfile(path) else None


def lookup(url: str) -> list[tuple[str, str]]:
    """Return ``[(url, title), …]``; playlists expand to their entries.
    Falls back to the bare URL if yt-dlp fails or isn't importable."""
    try:
        import yt_dlp
    except ImportError:
        return [(url, "")]
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": "list=" not in url or "/watch" in url}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            if info and info.get("_type") in ("url", "url_transparent") and info.get("url"):
                # e.g. watch?v=…&list=… resolves to the single video
                info = ydl.extract_info(info["url"], download=False, process=False)
    except Exception:
        return [(url, "")]
    if not info:
        return [(url, "")]
    entries = info.get("entries")
    if entries is None:
        return [(url, info.get("title") or "")]
    out = []
    for e in itertools.islice(entries, MAX_PLAYLIST):
        if not e:
            continue
        u = e.get("webpage_url") or e.get("url")
        if u and not u.startswith(("http://", "https://")) and e.get("ie_key") == "Youtube":
            u = f"https://www.youtube.com/watch?v={u}"
        if u and u.startswith(("http://", "https://")):
            out.append((u, e.get("title") or ""))
    return out or [(url, "")]


class _Signals(QObject):
    done = Signal(object, list)  # (token, [(url, title)])


class _Job(QRunnable):
    def __init__(self, url: str, token: object, signals: _Signals):
        super().__init__()
        self.url, self.token, self.signals = url, token, signals

    def run(self) -> None:
        self.signals.done.emit(self.token, lookup(self.url))


class Lookup(QObject):
    """Runs :func:`lookup` off the GUI thread."""

    done = Signal(object, list)

    def __init__(self):
        super().__init__()
        self._signals = _Signals()
        self._signals.done.connect(self.done)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)

    def start(self, url: str, token: object) -> None:
        self._pool.start(_Job(url, token, self._signals))
