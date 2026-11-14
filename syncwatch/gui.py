"""Control window: URL entry, shared queue, transport, and session panel."""

from __future__ import annotations

import json
import uuid

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton,
                               QSlider, QSplitter, QStyle, QVBoxLayout,
                               QWidget)

from . import media
from .captions import CaptionStyle
from .captions_dialog import CaptionStyleDialog
from .network import DEFAULT_TCP_PORT, Discovery, Mesh
from .player import Player, VideoWindow
from .state import SessionState, valid_url

RESYNC_MS = 5000
CAPTION_LANGS = ["en", "es", "fr", "de", "pt", "it", "ja", "ko", "zh", "ru", "ar", "hi"]
STATUS_MS = 1000


def fmt_time(t: float | None) -> str:
    if t is None or t < 0:
        t = 0
    t = int(t)
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def normalize_url(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    return text if valid_url(text) else None


class MainWindow(QMainWindow):
    def __init__(self, profile: str = "", port: int = DEFAULT_TCP_PORT):
        super().__init__()
        self.settings = QSettings("SyncWatch", f"SyncWatch-{profile}" if profile else "SyncWatch")
        self.peer_id = uuid.uuid4().hex[:16]
        name = self.settings.value("name", "") or f"viewer-{self.peer_id[:4]}"

        self.state = SessionState(self.peer_id)
        self.mesh = Mesh(self.peer_id, name, port)
        self.discovery = Discovery(self.mesh)
        self.lookup = media.Lookup()
        self.video = VideoWindow()
        self.video.show()
        self.player = Player(self.state, self.video)
        self._dragging = False
        self._local_status: dict = {}

        self.setWindowTitle("SyncWatch")
        self.resize(980, 620)
        self._build_ui(name)
        self._wire()

        on_top = self.settings.value("on_top", True, type=bool)
        self.on_top_box.setChecked(on_top)
        self.video.set_on_top(on_top)
        geo = self.settings.value("video_geometry")
        if geo is not None:
            self.video.restoreGeometry(geo)
        geo = self.settings.value("main_geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.volume.setValue(self.settings.value("volume", 80, type=int))
        self.cc_lang.setCurrentText(self.settings.value("caption_lang", "en"))
        self.cc_box.setChecked(self.settings.value("captions", False, type=bool))
        try:
            saved = json.loads(self.settings.value("caption_style", "{}"))
        except (TypeError, ValueError):
            saved = {}
        self.caption_style = CaptionStyle.from_dict(saved)
        self.player.set_caption_style(self.caption_style)
        self._style_dialog: CaptionStyleDialog | None = None
        self._apply_captions()

        self._refresh_all()
        self.log(f"Listening on port {self.mesh.port}")

    # -- UI construction --------------------------------------------------
    def _build_ui(self, name: str) -> None:
        st = self.style()
        icon = lambda sp: st.standardIcon(getattr(QStyle.StandardPixmap, sp))  # noqa: E731

        # left: url, queue, transport
        self.url_edit = QLineEdit(placeholderText="Paste a YouTube (or any yt-dlp) URL and press Enter")
        self.add_btn = QPushButton("Add")
        self.next_btn_add = QPushButton("Play next")
        url_row = QHBoxLayout()
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.add_btn)
        url_row.addWidget(self.next_btn_add)

        self.queue = QListWidget()
        self.queue.setAlternatingRowColors(True)
        self.play_now_btn = QPushButton("Play now")
        self.up_btn = QPushButton("Up")
        self.down_btn = QPushButton("Down")
        self.remove_btn = QPushButton("Remove")
        self.clear_btn = QPushButton("Clear played")
        q_row = QHBoxLayout()
        for b in (self.play_now_btn, self.up_btn, self.down_btn, self.remove_btn):
            q_row.addWidget(b)
        q_row.addStretch(1)
        q_row.addWidget(self.clear_btn)

        self.now_label = QLabel("Nothing playing")
        f = self.now_label.font()
        f.setBold(True)
        self.now_label.setFont(f)
        self.now_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setMinimumWidth(110)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        seek_row = QHBoxLayout()
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.time_label)

        self.prev_btn = QPushButton(icon("SP_MediaSkipBackward"), "")
        self.pause_btn = QPushButton(icon("SP_MediaPlay"), "")
        self.next_btn = QPushButton(icon("SP_MediaSkipForward"), "")
        for b, tip in ((self.prev_btn, "Previous"), (self.pause_btn, "Play / pause (Space in video window)"),
                       (self.next_btn, "Next")):
            b.setToolTip(tip)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 130)
        self.volume.setMaximumWidth(140)
        self.volume.setToolTip("Volume (local only)")
        self.cc_box = QCheckBox("CC")
        self.cc_box.setToolTip("Show captions, only for you (C in the video window)")
        self.cc_lang = QComboBox()
        self.cc_lang.setEditable(True)
        self.cc_lang.addItems(CAPTION_LANGS)
        self.cc_lang.setMaximumWidth(70)
        self.cc_lang.setToolTip("Caption language code, e.g. en, es, de, ja.\n"
                                "Uses the uploader's captions, else YouTube's auto-generated ones.")
        self.cc_style_btn = QPushButton("Style…")
        self.cc_style_btn.setToolTip("Caption font, colours, background box …")
        self.cc_label = QLabel("")
        self.on_top_box = QCheckBox("Video always on top")
        self.on_top_box.setToolTip("Toggle with T in the video window")
        self.show_video_box = QCheckBox("Show video")
        self.show_video_box.setChecked(True)
        self.sync_label = QLabel("")
        self.sync_label.setMinimumWidth(130)
        t_row = QHBoxLayout()
        for w in (self.prev_btn, self.pause_btn, self.next_btn):
            t_row.addWidget(w)
        t_row.addWidget(QLabel("Vol"))
        t_row.addWidget(self.volume)
        t_row.addWidget(self.sync_label)
        t_row.addWidget(self.cc_box)
        t_row.addWidget(self.cc_lang)
        t_row.addWidget(self.cc_style_btn)
        t_row.addWidget(self.cc_label)
        t_row.addStretch(1)
        t_row.addWidget(self.on_top_box)
        t_row.addWidget(self.show_video_box)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addLayout(url_row)
        ll.addWidget(self.queue, 1)
        ll.addLayout(q_row)
        ll.addWidget(self.now_label)
        ll.addLayout(seek_row)
        ll.addLayout(t_row)

        # right: session
        self.name_edit = QLineEdit(name)
        self.name_edit.setMaxLength(32)
        self.addr_label = QLabel()
        self.addr_label.setWordWrap(True)
        self.addr_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.session_label = QLabel()
        self.session_label.setWordWrap(True)
        self.peers_list = QListWidget()
        self.leave_btn = QPushButton("Leave session")

        sess = QGroupBox("Session")
        sl = QVBoxLayout(sess)
        nr = QHBoxLayout()
        nr.addWidget(QLabel("Your name"))
        nr.addWidget(self.name_edit, 1)
        sl.addLayout(nr)
        sl.addWidget(self.session_label)
        sl.addWidget(self.addr_label)
        sl.addWidget(QLabel("Peers"))
        sl.addWidget(self.peers_list, 1)
        sl.addWidget(self.leave_btn)

        self.found_list = QListWidget()
        self.join_found_btn = QPushButton("Join selected")
        self.join_edit = QLineEdit(placeholderText="host:port")
        self.join_btn = QPushButton("Join")
        jr = QHBoxLayout()
        jr.addWidget(self.join_edit, 1)
        jr.addWidget(self.join_btn)
        join = QGroupBox("Join another session")
        jl = QVBoxLayout(join)
        jl.addWidget(QLabel("Found on your network"))
        jl.addWidget(self.found_list, 1)
        jl.addWidget(self.join_found_btn)
        jl.addWidget(QLabel("Or connect directly"))
        jl.addLayout(jr)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(sess, 3)
        rl.addWidget(join, 2)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        right.setMinimumWidth(300)
        split.setSizes([620, 360])
        self.setCentralWidget(split)

    def _wire(self) -> None:
        self.url_edit.returnPressed.connect(lambda: self._add(False))
        self.add_btn.clicked.connect(lambda: self._add(False))
        self.next_btn_add.clicked.connect(lambda: self._add(True))
        self.queue.itemDoubleClicked.connect(lambda it: self._play(it.data(Qt.ItemDataRole.UserRole)))
        self.play_now_btn.clicked.connect(lambda: self._play(self._selected()))
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(+1))
        self.remove_btn.clicked.connect(self._remove)
        self.clear_btn.clicked.connect(lambda: self.state.clear_played() and self._changed())
        self.prev_btn.clicked.connect(lambda: self.state.advance(-1) and self._changed())
        self.next_btn.clicked.connect(lambda: self.state.advance(+1) and self._changed())
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.volume.valueChanged.connect(self.player.set_volume)
        self.on_top_box.toggled.connect(self._set_on_top)
        self.show_video_box.toggled.connect(self.video.setVisible)

        self.video.toggle_pause.connect(self._toggle_pause)
        self.video.toggle_on_top.connect(lambda: self.on_top_box.toggle())
        self.video.toggle_captions.connect(lambda: self.cc_box.toggle())
        self.cc_box.toggled.connect(lambda _on: self._apply_captions())
        self.cc_lang.lineEdit().editingFinished.connect(self._apply_captions)
        self.cc_lang.activated.connect(lambda _i: self._apply_captions())
        self.cc_style_btn.clicked.connect(self._open_caption_style)
        self.video.seek_relative.connect(
            lambda d: self.state.seek(self.state.play.position() + d) and self._changed())
        self.video.closed.connect(lambda: self.show_video_box.setChecked(False))

        self.player.ended.connect(self._on_ended)
        self.player.failed.connect(self._on_failed)
        self.player.status.connect(self._on_local_status)
        self.lookup.done.connect(self._on_lookup)

        self.mesh.state_received.connect(self._on_remote_state)
        self.mesh.peer_connected.connect(lambda l: self.mesh.send_state(self.state.to_wire(), l))
        self.mesh.joined.connect(self._on_joined)
        self.mesh.peers_changed.connect(self._refresh_peers)
        self.mesh.status_received.connect(self._refresh_peers)
        self.mesh.log.connect(self.log)
        self.discovery.changed.connect(self._refresh_found)

        self.name_edit.editingFinished.connect(self._rename)
        self.leave_btn.clicked.connect(self._leave)
        self.join_btn.clicked.connect(self._join_manual)
        self.join_edit.returnPressed.connect(self._join_manual)
        self.join_found_btn.clicked.connect(self._join_found)
        self.found_list.itemDoubleClicked.connect(lambda _i: self._join_found())

        QTimer(self, interval=RESYNC_MS, timeout=self._resync).start()
        QTimer(self, interval=STATUS_MS, timeout=self._send_status).start()
        QTimer(self, interval=250, timeout=self._refresh_transport).start()

    # -- helpers ----------------------------------------------------------
    def log(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 20000)

    def _selected(self) -> str | None:
        it = self.queue.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _changed(self) -> None:
        """A local edit happened: tell peers and redraw."""
        self.mesh.send_state(self.state.to_wire())
        self._refresh_all()

    # -- queue actions ----------------------------------------------------
    def _add(self, play_next: bool) -> None:
        url = normalize_url(self.url_edit.text())
        if url is None:
            self.log("That doesn't look like a URL")
            return
        self.url_edit.clear()
        is_playlist = "list=" in url and "/watch" not in url
        if is_playlist:
            self.log("Fetching playlist …")
            self.lookup.start(url, ("playlist", None))
            return
        item = self.state.add(url, added_by=self.mesh.name, play_next=play_next)
        self.lookup.start(url, ("title", item.id))
        self._changed()

    def _on_lookup(self, token, results: list) -> None:
        kind, item_id = token
        if kind == "title":
            if results and len(results) == 1 and self.state.set_title(item_id, results[0][1]):
                self._changed()
            return
        for url, title in results:
            if valid_url(url):
                self.state.add(url, title=title, added_by=self.mesh.name)
        self.log(f"Added {len(results)} videos")
        self._changed()

    def _play(self, item_id: str | None) -> None:
        if item_id and self.state.play_item(item_id):
            self._changed()

    def _move(self, step: int) -> None:
        item_id = self._selected()
        if item_id and self.state.move(item_id, step):
            self._changed()

    def _remove(self) -> None:
        item_id = self._selected()
        if item_id and self.state.remove(item_id):
            self._changed()

    # -- transport --------------------------------------------------------
    def _toggle_pause(self) -> None:
        if self.state.set_paused(not self.state.play.paused):
            self._changed()

    def _seek_released(self) -> None:
        self._dragging = False
        if self.state.seek(self.seek.value() / 1000):
            self._changed()

    def _apply_captions(self) -> None:
        lang = "".join(c for c in self.cc_lang.currentText().strip()
                       if c.isalnum() or c == "-")[:12] or "en"
        if self.cc_lang.currentText() != lang:
            self.cc_lang.setCurrentText(lang)
        self.player.set_captions(self.cc_box.isChecked(), lang)
        self.settings.setValue("captions", self.cc_box.isChecked())
        self.settings.setValue("caption_lang", lang)

    def _open_caption_style(self) -> None:
        if self._style_dialog is None:
            self._style_dialog = CaptionStyleDialog(self.caption_style, self)
            self._style_dialog.style_changed.connect(self._set_caption_style)
        self._style_dialog.show()
        self._style_dialog.raise_()
        self._style_dialog.activateWindow()
        if not self.cc_box.isChecked():
            self.log("Tip: turn on CC to see the style on the video as you change it")

    def _set_caption_style(self, style: CaptionStyle) -> None:
        self.caption_style = style
        self.player.set_caption_style(style)
        self.settings.setValue("caption_style", json.dumps(style.to_dict()))

    def _set_on_top(self, on: bool) -> None:
        self.video.set_on_top(on)
        self.settings.setValue("on_top", on)

    def _on_ended(self, item_id: str) -> None:
        if self.state.advance(+1, from_item=item_id):
            self._changed()

    def _on_failed(self, item_id: str, msg: str) -> None:
        it = self.state.items.get(item_id)
        self.log(f"Couldn't play {it.title or it.url if it else 'video'}: {msg}")
        if not self.mesh.peers() and self.state.advance(+1, from_item=item_id):
            self._changed()

    # -- network ----------------------------------------------------------
    def _on_remote_state(self, msg: dict, delay: float) -> None:
        if self.state.merge(msg, delay):
            # pass it on so partially-connected meshes still converge
            self.mesh.send_state(self.state.to_wire())
            self._refresh_all()

    def _resync(self) -> None:
        if self.mesh.peers():
            self.mesh.send_state(self.state.to_wire())

    def _on_joined(self) -> None:
        self.state.reset()
        self.log(f"Joined {self.mesh.session_label}'s session")
        self._refresh_all()

    def _send_status(self) -> None:
        s = self._local_status
        self.mesh.broadcast({"type": "status", "name": self.mesh.name,
                             "pos": s.get("pos"), "drift": s.get("drift"),
                             "state": s.get("state"), "item": s.get("item")})

    def _on_local_status(self, s: dict) -> None:
        self._local_status = s

    def _rename(self) -> None:
        name = self.name_edit.text().strip()[:32] or self.mesh.name
        self.name_edit.setText(name)
        if name != self.mesh.name:
            self.mesh.name = name
            if not self.mesh.peers():
                self.mesh.session_label = name
            self.settings.setValue("name", name)
            self._refresh_peers()

    def _confirm_join(self) -> bool:
        if not self.state.queue():
            return True
        return QMessageBox.question(
            self, "Join session",
            "Joining replaces your current queue with the other session's. Continue?"
        ) == QMessageBox.StandardButton.Yes

    def _join_manual(self) -> None:
        text = self.join_edit.text().strip()
        host, _, port = text.rpartition(":")
        if not host:
            host, port = text, "47801"
        host = host.strip("[]")
        if not host or not port.isdigit() or not 0 < int(port) < 65536:
            self.log("Enter host:port, e.g. 192.168.1.20:47801")
            return
        if self._confirm_join():
            self.mesh.leave()
            self.mesh.connect_to(host, int(port), join=True)

    def _join_found(self) -> None:
        it = self.found_list.currentItem()
        if it is None:
            return
        s = it.data(Qt.ItemDataRole.UserRole)
        if self._confirm_join():
            self.mesh.leave()
            self.mesh.connect_to(s["host"], s["port"], join=True)

    def _leave(self) -> None:
        self.mesh.leave()
        self.log("Left the session (your queue is kept)")
        self._refresh_all()

    # -- refresh ----------------------------------------------------------
    def _refresh_all(self) -> None:
        self._refresh_queue()
        self._refresh_transport()
        self._refresh_peers()
        self._refresh_found()

    def _refresh_queue(self) -> None:
        sel = self._selected()
        cur = self.state.current()
        played = cur is not None
        self.queue.clear()
        for it in self.state.queue():
            is_cur = cur is not None and it.id == cur.id
            text = ("▶  " if is_cur else "    ") + (it.title or it.url)
            if it.added_by:
                text += f"   · {it.added_by}"
            row = QListWidgetItem(text)
            row.setData(Qt.ItemDataRole.UserRole, it.id)
            row.setToolTip(it.url)
            if is_cur:
                f = QFont(row.font())
                f.setBold(True)
                row.setFont(f)
                played = False
            elif played:
                row.setForeground(QBrush(QColor(128, 128, 128)))
            self.queue.addItem(row)
            if it.id == sel:
                self.queue.setCurrentItem(row)
        cur_title = (cur.title or cur.url) if cur else None
        self.now_label.setText(cur_title or "Nothing playing")
        self.video.setWindowTitle(f"{cur_title} — SyncWatch" if cur_title else "SyncWatch — Video")

    def _refresh_transport(self) -> None:
        p = self.state.play
        cur = self.state.current()
        s = self._local_status
        dur = s.get("duration") if s.get("item") == (cur.id if cur else None) else None
        pos = p.position() if cur else 0.0
        st = self.style()
        self.pause_btn.setIcon(st.standardIcon(
            QStyle.StandardPixmap.SP_MediaPlay if p.paused or not cur
            else QStyle.StandardPixmap.SP_MediaPause))
        if not self._dragging:
            self.seek.setRange(0, int((dur or 0) * 1000))
            self.seek.setValue(int(max(0.0, pos) * 1000))
        shown = self.seek.value() / 1000 if self._dragging else pos
        self.time_label.setText(f"{fmt_time(shown)} / {fmt_time(dur)}")
        self.cc_label.setText(self.player.caption_status())
        state = s.get("state") or "idle"
        if not cur:
            self.sync_label.setText("")
        elif state == "playing":
            self.sync_label.setText(f"in sync ({s.get('drift', 0) * 1000:+.0f} ms)")
        elif state == "waiting":
            self.sync_label.setText(f"starting in {-pos:.0f}s")
        else:
            self.sync_label.setText(state)

    def _refresh_peers(self) -> None:
        peers = self.mesh.peers()
        self.session_label.setText(
            f"In <b>{self.mesh.session_label}</b>'s session · {len(peers) + 1} viewer(s)"
            if peers else "Not connected — others can join you, or join someone →")
        addrs = ", ".join(f"{a}:{self.mesh.port}" for a in self.mesh.local_addresses())
        self.addr_label.setText(f"Others can join you at: {addrs or f'port {self.mesh.port}'}")
        self.addr_label.setToolTip("Any of these that the other person can reach (LAN, Tailscale, …)")
        self.peers_list.clear()
        self.peers_list.addItem(f"{self.mesh.name} (you)")
        for l in peers:
            rtt = f"ping {l.rtt * 1000:.0f} ms" if l.rtt is not None else "ping …"
            st = l.status.get("state") or ""
            drift = l.status.get("drift")
            if st == "playing" and isinstance(drift, (int, float)):
                st = f"in sync {drift * 1000:+.0f} ms"
            row = QListWidgetItem(f"{l.name}  ·  {rtt}  ·  {st}" if st else f"{l.name}  ·  {rtt}")
            row.setToolTip(f"{l.host}:{l.listen_port}")
            self.peers_list.addItem(row)
        self.leave_btn.setEnabled(bool(peers))

    def _refresh_found(self) -> None:
        sel = self.found_list.currentItem()
        sel_sess = sel.data(Qt.ItemDataRole.UserRole)["session"] if sel else None
        self.found_list.clear()
        for s in self.discovery.sessions():
            members = ", ".join(sorted(s["members"]))
            row = QListWidgetItem(f"{s['label']}'s session — {members}")
            row.setData(Qt.ItemDataRole.UserRole, s)
            self.found_list.addItem(row)
            if s["session"] == sel_sess:
                self.found_list.setCurrentItem(row)
        self.join_found_btn.setEnabled(self.found_list.count() > 0)

    # -- lifetime ---------------------------------------------------------
    def closeEvent(self, e):
        self.settings.setValue("main_geometry", self.saveGeometry())
        self.settings.setValue("video_geometry", self.video.saveGeometry())
        self.settings.setValue("volume", self.volume.value())
        self.mesh.leave()
        self.player.shutdown()
        e.accept()
        QApplication.quit()
