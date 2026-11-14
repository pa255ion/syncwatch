"""Three meshes in one process: C joins B and must also link up with A."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication  # noqa: E402

from syncwatch.network import Mesh  # noqa: E402
from syncwatch.state import SessionState  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])


def spin(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        app.processEvents()
        time.sleep(0.005)
    return cond()


class Node:
    def __init__(self, pid, port):
        self.state = SessionState(pid)
        self.mesh = Mesh(pid, pid, port)
        self.mesh.state_received.connect(self.on_state)
        self.mesh.peer_connected.connect(lambda l: self.mesh.send_state(self.state.to_wire(), l))
        self.mesh.joined.connect(self.state.reset)

    def on_state(self, msg, delay):
        if self.state.merge(msg, delay):
            self.mesh.send_state(self.state.to_wire())

    def ids(self):
        return sorted(l.peer_id for l in self.mesh.peers())


class MeshTests(unittest.TestCase):
    def test_join_forms_full_mesh_and_converges(self):
        a, b, c = Node("a" * 16, 48101), Node("b" * 16, 48121), Node("c" * 16, 48141)
        a.state.add("https://example.com/1")
        b.state.add("https://example.com/only-b")  # dropped: b joins a
        b.mesh.connect_to("127.0.0.1", a.mesh.port, join=True)
        self.assertTrue(spin(lambda: [i.url for i in b.state.queue()] == ["https://example.com/1"]))
        c.mesh.connect_to("127.0.0.1", b.mesh.port, join=True)
        self.assertTrue(spin(lambda: all(len(n.mesh.peers()) == 2 for n in (a, b, c))),
                        [n.ids() for n in (a, b, c)])
        self.assertEqual(a.mesh.session, c.mesh.session)
        # an edit on c reaches a directly
        c.state.add("https://example.com/2")
        c.mesh.send_state(c.state.to_wire())
        self.assertTrue(spin(lambda: len(a.state.queue()) == 2))
        self.assertEqual([i.url for i in a.state.queue()],
                         ["https://example.com/1", "https://example.com/2"])
        # b leaves; a and c stay linked
        b.mesh.leave()
        self.assertTrue(spin(lambda: a.ids() == [c.mesh.peer_id] and c.ids() == [a.mesh.peer_id]))

    def _join_log(self, host, port, timeout=12.0):
        n = Node("d" * 16, 48161)
        msgs = []
        n.mesh.log.connect(msgs.append)
        n.mesh.connect_to(host, port, join=True)
        spin(lambda: len(msgs) >= 2 and "Joining" not in msgs[-1]
             and "waiting" not in msgs[-1], timeout)
        n.mesh.server.close()
        return msgs[-1]

    def test_join_refused_says_why(self):
        self.assertIn("refused", self._join_log("127.0.0.1", 1).lower())

    def test_join_to_non_syncwatch_port_says_so(self):
        from PySide6.QtNetwork import QHostAddress, QTcpServer
        dummy = QTcpServer()
        dummy.listen(QHostAddress.SpecialAddress.LocalHost, 0)
        msg = self._join_log("127.0.0.1", dummy.serverPort())
        self.assertIn("didn't answer like SyncWatch", msg)

    def test_join_cancelled_by_leave_is_quiet(self):
        n = Node("e" * 16, 48181)
        msgs = []
        n.mesh.log.connect(msgs.append)
        n.mesh.connect_to("10.255.255.1", 47801, join=True)  # never answers
        n.mesh.leave()
        spin(lambda: False, 0.3)
        self.assertEqual(msgs, ["Joining 10.255.255.1:47801 …"])


if __name__ == "__main__":
    unittest.main()
