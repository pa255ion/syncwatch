"""Video window (libmpv embedded in Qt) and the loop that keeps local
playback in step with the shared playback state."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import media
from .captions import CaptionStyle, to_mpv
from .state import SessionState, now

if os.name == "nt":
    # find libmpv-2.dll in the bundle, next to the app or package, or the cwd;
    # PATH also lets mpv's yt-dlp find a bundled deno.exe
    for d in (media.bundle_dir(), os.path.dirname(sys.executable),
              os.path.dirname(os.path.abspath(__file__)),
              os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.getcwd()):
        if d is None:
            continue
        if os.path.isdir(d):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(d)
            except (OSError, AttributeError):
                pass

import mpv  # noqa: E402  (needs the DLL search path above on Windows)

TICK_MS = 100
HARD_DRIFT = 0.8      # beyond this, seek instead of nudging speed
SOFT_DRIFT = 0.04     # below this, play at normal speed
MAX_NUDGE = 0.08      # max +/- speed change when correcting drift
SYNC_SETTLE = 0.01    # release a hold once we're this close
MAX_RETRIES = 2
WATCHED = ("time-pos", "duration", "seeking", "eof-reached", "paused-for-cache",
           "pause", "speed", "volume", "track-list")


class VideoWindow(QWidget):
    """Top-level window hosting the mpv video surface."""

    toggle_pause = Signal()
    toggle_on_top = Signal()
    toggle_captions = Signal()
    seek_relative = Signal(float)
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SyncWatch — Video")
        self.resize(854, 480)
        self.setMinimumSize(240, 135)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("black"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self.surface = QWidget(self)
        self.surface.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.surface.setPalette(pal)
        self.surface.setAutoFillBackground(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.surface)

        for key, fn in (("Space", self.toggle_pause.emit),
                        ("T", self.toggle_on_top.emit),
                        ("C", self.toggle_captions.emit),
                        ("F", self.toggle_fullscreen),
                        ("F11", self.toggle_fullscreen),
                        ("Escape", self.showNormal),
                        ("Left", lambda: self.seek_relative.emit(-10)),
                        ("Right", lambda: self.seek_relative.emit(10))):
            QShortcut(QKeySequence(key), self, activated=fn)

    def surface_id(self) -> int:
        return int(self.surface.winId())

    def set_on_top(self, on: bool) -> None:
        # Change the flag on the existing native window. QWidget.setWindowFlag
        # would recreate it and orphan mpv's render target.
        handle = self.windowHandle()
        if handle is not None:
            handle.setFlag(Qt.WindowType.WindowStaysOnTopHint, on)
            if on:
                handle.raise_()
        else:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def mouseDoubleClickEvent(self, e):
        self.toggle_fullscreen()

    def closeEvent(self, e):
        # closing just hides it; the control window owns the app lifetime
        e.ignore()
        self.hide()
        self.closed.emit()


class Player(QObject):
    """Drives mpv so that it follows ``state.play``."""

    ended = Signal(str)            # item id whose playback finished
    failed = Signal(str, str)      # item id, message
    status = Signal(dict)          # for the UI and for peers
    _mpv_end = Signal(int, int)    # (reason, error) from mpv's thread

    def __init__(self, state: SessionState, window: VideoWindow):
        super().__init__()
        self.state = state
        self.window = window
        opts = dict(
            wid=str(window.surface_id()),
            ytdl=True,
            ytdl_format="bestvideo[height<=?1080]+bestaudio/best",
            keep_open="yes", idle="yes", force_window="yes",
            input_default_bindings=False, input_vo_keyboard=False,
            input_cursor=False, cursor_autohide="no", osc=False,
            hr_seek="yes", hwdec="auto-safe", cache="yes",
            osd_level=1,
        )
        path = media.ytdlp_path()
        if path:
            opts["script_opts"] = f"ytdl_hook-ytdl_path={path}"
        self.captions = False
        self.caption_lang = "en"
        self._loaded_caption_lang: str | None = None
        self._caption_picked = False
        opts["ytdl_raw_options"] = self._raw_options()
        opts["sub_visibility"] = "no"
        # escape hatch for driver trouble, e.g. SYNCWATCH_MPV_OPTS="vo=gpu,gpu-api=opengl"
        for kv in filter(None, os.environ.get("SYNCWATCH_MPV_OPTS", "").split(",")):
            k, _, v = kv.partition("=")
            opts[k.strip().replace("-", "_")] = v.strip() or "yes"
        self.mpv = mpv.MPV(log_handler=self._mpv_log, loglevel="warn", **opts)
        self._mpv_end.connect(self._on_end_file)

        # Never block the GUI thread on mpv: mirror the properties we need
        # (written from mpv's event thread) and send changes asynchronously.
        self._props: dict[str, object] = {}
        self._pos_at = 0.0
        for name in WATCHED:
            self.mpv.observe_property(name, self._on_prop)

        @self.mpv.event_callback("playback-restart")
        def _restarted(_event):
            self._props["seeking"] = False  # a seek or load finished

        @self.mpv.event_callback("end-file")
        def _end(event):
            d = event.data
            self._mpv_end.emit(int(d.reason), int(d.error))

        self.loaded: str | None = None      # item id loaded in mpv
        self.loaded_url: str | None = None
        self.holding = False                # paused locally, waiting for sync point
        self.seek_started: float | None = None
        self.seek_lead = 0.8                # how far ahead to seek (adapts)
        self.ended_sent = False
        self.retries: dict[str, int] = {}
        self.error: str | None = None
        self.drift = 0.0
        self.local_state = "idle"

        self._timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self._timer.start()
        self._status_timer = QTimer(self, interval=500, timeout=self._emit_status)
        self._status_timer.start()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _mpv_log(level: str, prefix: str, text: str) -> None:
        if sys.stderr is not None:  # runs on mpv's thread; just print
            print(f"mpv[{level}] {prefix}: {text.rstrip()}", file=sys.stderr)

    def _on_prop(self, name: str, value) -> None:
        if name == "time-pos":
            self._pos_at = now()
        self._props[name] = value

    def _get(self, name: str):
        return self._props.get(name)

    def _pos(self) -> float | None:
        """time-pos extrapolated to now (it is only reported once per frame)."""
        pos = self._props.get("time-pos")
        if pos is None or self._props.get("pause") or self._props.get("paused-for-cache"):
            return pos
        return pos + (now() - self._pos_at) * (self._props.get("speed") or 1.0)

    def _set(self, name: str, value) -> None:
        if self._props.get(name) == value:
            return
        self._props[name] = value  # optimistic; the observer confirms
        arg = ("yes" if value else "no") if isinstance(value, bool) else str(value)
        try:
            self.mpv.command_async("set", name, arg)
        except Exception:
            pass

    def set_volume(self, v: int) -> None:
        self._set("volume", float(v))

    # -- captions (per viewer; never shared) ------------------------------
    def _raw_options(self) -> str:
        # yt-dlp lists captions in the chosen language, falling back to
        # YouTube's auto-generated ones; mpv adds them as lazily-loaded tracks.
        # sub-langs is a regex ("en.*" also matches en-US, en-orig, ...).
        lang = "".join(c for c in self.caption_lang if c.isalnum() or c == "-") or "en"
        opts = ["write-auto-subs=", f"sub-langs={lang}.*"]
        deno = media.bundled_deno()
        if deno:
            opts.append(f"js-runtimes=deno:{deno}")
        return ",".join(opts)

    def set_captions(self, on: bool, lang: str) -> None:
        lang = lang.strip() or "en"
        lang_changed = lang != self.caption_lang
        self.captions, self.caption_lang = on, lang
        self._set("sub-visibility", on)
        if lang_changed:
            self._set("ytdl-raw-options", self._raw_options())
        if on and self.loaded and self._loaded_caption_lang != lang:
            # this video's caption list was fetched for another language
            self.loaded_url = None  # next tick reloads at the shared position

    def set_caption_style(self, style: CaptionStyle) -> None:
        for name, value in to_mpv(style).items():
            self._set(name, value)

    def caption_tracks(self) -> list[dict]:
        tracks = self._props.get("track-list") or []
        return [t for t in tracks if isinstance(t, dict) and t.get("type") == "sub"]

    @staticmethod
    def _translated(track: dict) -> bool:
        # YouTube machine translations are fetched with &tlang=; YouTube
        # rate-limits those (HTTP 429) far more than real caption tracks
        return "tlang=" in (track.get("external-filename") or "")

    def _caption_rank(self, track: dict) -> tuple | None:
        """Lower is better; None if the track isn't in the wanted language."""
        lang = (track.get("lang") or "").lower()
        want = self.caption_lang.lower()
        if not (lang == want or lang.split("-")[0] == want):
            return None
        auto = "kind=asr" in (track.get("external-filename") or "")
        return (self._translated(track), auto, lang != want, track.get("id") or 0)

    def _pick_caption_track(self) -> None:
        """Choose the track ourselves instead of mpv's language matching,
        which happily picks a machine translation over a real track."""
        self._caption_picked = True
        ranked = sorted((r, t) for t in self.caption_tracks()
                        if (r := self._caption_rank(t)) is not None)
        if ranked:
            try:
                self.mpv.command_async("set", "sid", str(ranked[0][1]["id"]))
            except Exception:
                pass

    def caption_status(self) -> str:
        """Short text for the UI: which caption track is showing, if any."""
        if not self.captions or not self.loaded or self._get("time-pos") is None:
            return ""
        sel = next((t for t in self.caption_tracks() if t.get("selected")), None)
        if sel is None:
            return f"no {self.caption_lang} captions"
        label = sel.get("lang") or sel.get("title") or "?"
        return f"captions: {label}" + (" (machine-translated)" if self._translated(sel) else "")

    def _seek(self, target: float) -> None:
        self._set("pause", True)
        self.holding = True
        self.seek_started = now()
        self._props["seeking"] = True  # until mpv reports otherwise
        try:
            self.mpv.command_async("seek", f"{max(0.0, target):.3f}", "absolute+exact")
        except Exception:
            self.seek_started = None

    def _load(self, item_id: str, url: str, start: float) -> None:
        self.loaded, self.loaded_url = item_id, url
        self.ended_sent = False
        self.error = None
        self.holding = True
        self.seek_started = now()
        self._set("pause", True)
        self._set("speed", 1.0)
        self._props.pop("time-pos", None)
        self._props.pop("duration", None)
        self._props["eof-reached"] = False
        opts = {"start": f"{start:.3f}"} if start > 0.5 else {}
        opts["sid"] = "no"  # per file; _pick_caption_track chooses once CC is on
        self._caption_picked = False
        self._props.pop("track-list", None)
        self._loaded_caption_lang = self.caption_lang
        try:
            self.mpv.loadfile(url, "replace", **opts)
        except Exception as e:
            self._fail(item_id, str(e))

    def _stop(self) -> None:
        if self.loaded is not None:
            try:
                self.mpv.command("stop")
            except Exception:
                pass
        self.loaded = self.loaded_url = None
        self.holding = False
        self.seek_started = None

    def _fail(self, item_id: str, msg: str) -> None:
        n = self.retries.get(item_id, 0) + 1
        self.retries[item_id] = n
        if n < MAX_RETRIES:
            self.loaded_url = None  # next tick reloads (same id keeps the count)
            return
        self.error = msg
        self.failed.emit(item_id, msg)

    def _on_end_file(self, reason: int, error: int) -> None:
        if reason == mpv.MpvEventEndFile.ERROR and self.loaded:
            self._fail(self.loaded, f"mpv could not play this (error {error})")

    # -- the sync loop ----------------------------------------------------
    def _tick(self) -> None:
        cur = self.state.current()
        p = self.state.play
        if cur is None:
            self._stop()
            self.local_state = "idle"
            return
        if self.error and self.loaded == cur.id:
            self.local_state = "error"
            return
        exp = p.position()
        if self.loaded != cur.id or self.loaded_url != cur.url:
            if self.loaded != cur.id:
                self.retries.pop(cur.id, None)
            self._load(cur.id, cur.url, exp + self.seek_lead if not p.paused else exp)
            self.local_state = "loading"
            return

        pos = self._pos()
        dur = self._get("duration")
        if pos is None or self._get("seeking"):
            self.local_state = "loading" if self.seek_started else "buffering"
            return
        if self.captions and not self._caption_picked:
            self._pick_caption_track()
        if self.seek_started is not None:
            # a seek/load just completed: learn how long that takes
            took = now() - self.seek_started
            self.seek_lead = min(10.0, max(0.3, 0.6 * self.seek_lead + 0.4 * (took + 0.2)))
            self.seek_started = None

        if p.paused:
            self.holding = False
            self._set("pause", True)
            self._set("speed", 1.0)
            target = max(0.0, min(exp, dur) if dur else exp)
            self.drift = pos - target
            if abs(self.drift) > 0.05:
                self._seek(target)
                self.holding = False
            self.local_state = "paused"
            return

        if exp < 0:  # countdown before a new video starts
            self._set("pause", True)
            self.drift = 0.0
            self.local_state = "waiting"
            if pos > 0.5:
                self._seek(0.0)
            return

        if self._get("eof-reached") or (dur and exp > dur + 1.0):
            self._set("pause", True)
            self.local_state = "ended"
            if not self.ended_sent:
                self.ended_sent = True
                self.ended.emit(cur.id)
            return

        d = pos - exp
        self.drift = d
        if self.holding:
            if d > SYNC_SETTLE and d < HARD_DRIFT * 4:
                self.local_state = "syncing"
                return  # ahead: wait for the group to catch up
            self.holding = False

        if abs(d) > HARD_DRIFT:
            if 0 < d < 3.0:
                self._set("pause", True)  # slightly ahead: just wait
                self.holding = True
            else:
                self._seek(exp + self.seek_lead)
            self.local_state = "syncing"
            return

        self._set("pause", False)
        if abs(d) > SOFT_DRIFT:
            speed = 1.0 - max(-MAX_NUDGE, min(MAX_NUDGE, d * 0.5))
        else:
            speed = 1.0
        if abs((self._get("speed") or 1.0) - speed) > 0.005:
            self._set("speed", round(speed, 3))
        self.local_state = "buffering" if self._get("paused-for-cache") else "playing"

    def _emit_status(self) -> None:
        self.status.emit({
            "item": self.loaded, "pos": self._pos(),
            "duration": self._get("duration"), "drift": round(self.drift, 3),
            "state": self.local_state, "error": self.error,
        })

    def shutdown(self) -> None:
        self._timer.stop()
        try:
            self.mpv.terminate()
        except Exception:
            pass
