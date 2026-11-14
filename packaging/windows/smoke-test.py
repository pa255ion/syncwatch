"""Native Linux peer for smoke-test.sh: joins the Windows exe, queues a
video, pauses and seeks, and prints what the exe reports about itself."""
import json
import locale
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
locale.setlocale(locale.LC_NUMERIC, "C")
from syncwatch.gui import MainWindow  # noqa: E402

URL = os.environ.get("SMOKE_URL", "https://www.youtube.com/watch?v=aqz-KE-bpKQ")
w = MainWindow(profile="smoketest", port=47912)
w.show()
t0 = time.time()


def at(t, fn):
    QTimer.singleShot(int(t * 1000), fn)


def log(*a):
    print(f"[{time.time() - t0:6.2f}]", *a, flush=True)


def report():
    pos = w.player._pos()
    log(json.dumps({
        "linux": [w.player.local_state, pos and round(pos, 2), round(w.player.drift, 3)],
        "exe": [(l.name, l.rtt and round(l.rtt * 1000), l.status) for l in w.mesh.peers()],
        "lan": [(s["label"], s["port"]) for s in w.discovery.sessions()]}))


QTimer(w, interval=2000, timeout=report).start()
at(3, lambda: (w.join_edit.setText("127.0.0.1:47801"), w._join_manual()))
at(7, lambda: (w.url_edit.setText(URL), w._add(False)))
at(45, lambda: (log("PAUSE"), w._toggle_pause()))
at(52, lambda: (log("RESUME + SEEK 200"), w._toggle_pause(), w.state.seek(200) and w._changed()))
at(75, w.close)
app.exec()
