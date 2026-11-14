"""Peer-to-peer mesh over TCP plus LAN discovery over UDP broadcast.

Wire format: one JSON object per line, UTF-8.

Every peer listens on a TCP port and connects to every other peer in its
session (full mesh). When a peer joins, it is told the addresses of the
other members and connects to them too, so no peer is special and the
session survives any member leaving. If two peers end up with two
connections to each other, both keep the one started by the lower peer id.
"""

from __future__ import annotations

import json
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import (QAbstractSocket, QHostAddress, QNetworkInterface,
                               QTcpServer, QTcpSocket, QUdpSocket)

PROTO = 1
DEFAULT_TCP_PORT = 47801
DISCOVERY_PORT = 47800
MAX_LINE = 4 * 1024 * 1024
JOIN_TIMEOUT_MS = 10000
PING_INTERVAL_MS = 2000
BEACON_INTERVAL_MS = 2000
DISCOVERY_TTL = 7.0


def host_str(addr: QHostAddress) -> str:
    """Plain dotted IPv4 for IPv4(-mapped) addresses, else Qt's string."""
    ip4 = addr.toIPv4Address()
    return QHostAddress(ip4).toString() if ip4 else addr.toString()


class Link(QObject):
    """One TCP connection to another peer."""

    message = Signal(object, dict)  # (link, msg)
    closed = Signal(object)

    def __init__(self, sock: QTcpSocket, local_id: str, outbound: bool, join: bool = False):
        super().__init__()
        self.sock = sock
        self._local_id = local_id
        self.outbound = outbound
        self.join = join            # we asked to join the remote's session
        self.peer_id: str | None = None
        self.name = ""
        self.listen_port = 0
        self.rtt: float | None = None
        self.status: dict = {}
        self._buf = bytearray()
        self._closed = False
        self.sock_was_connected = sock.state() == QAbstractSocket.SocketState.ConnectedState
        sock.connected.connect(lambda: setattr(self, "sock_was_connected", True))
        sock.readyRead.connect(self._on_read)
        sock.disconnected.connect(self.close)
        self.error = ""
        self.quiet = False  # closed on purpose; don't report it as a failure
        sock.errorOccurred.connect(self._on_error)

    @property
    def host(self) -> str:
        return host_str(self.sock.peerAddress())

    @property
    def initiator(self) -> str:
        """Peer id of whoever opened this connection."""
        return self._local_id if self.outbound else (self.peer_id or "")

    def _on_error(self, _e) -> None:
        self.error = self.sock.errorString()
        self.close()

    def send(self, msg: dict) -> None:
        if self._closed or self.sock.state() != QAbstractSocket.SocketState.ConnectedState:
            return
        data = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        self.sock.write(data)

    def _on_read(self) -> None:
        self._buf += bytes(self.sock.readAll())
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                if len(self._buf) > MAX_LINE:
                    self.close()
                return
            line = bytes(self._buf[:nl])
            del self._buf[:nl + 1]
            try:
                msg = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(msg, dict) and isinstance(msg.get("type"), str):
                self.message.emit(self, msg)
            if self._closed:
                return

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.abort()
            self.sock.deleteLater()
        except RuntimeError:  # Qt already destroyed it (app shutdown)
            pass
        self.closed.emit(self)


class Mesh(QObject):
    """Owns the listening socket and all links of the current session."""

    peers_changed = Signal()
    state_received = Signal(dict, float)   # (state msg, one-way delay)
    joined = Signal()                      # we adopted a remote session
    peer_connected = Signal(object)        # link (send it our state)
    status_received = Signal()
    log = Signal(str)

    def __init__(self, peer_id: str, name: str, port: int = DEFAULT_TCP_PORT):
        super().__init__()
        self.peer_id = peer_id
        self.name = name
        self.session = uuid.uuid4().hex[:12]
        self.session_label = name
        self.links: list[Link] = []
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._on_incoming)
        for p in [port, *range(port + 1, port + 20), 0]:
            if self.server.listen(QHostAddress.SpecialAddress.Any, p):
                break
        self.port = self.server.serverPort()
        self._ping = QTimer(self, interval=PING_INTERVAL_MS, timeout=self._ping_all)
        self._ping.start()

    # -- public -----------------------------------------------------------
    def peers(self) -> list[Link]:
        return [l for l in self.links if l.peer_id]

    def connect_to(self, host: str, port: int, join: bool) -> None:
        sock = QTcpSocket(self)
        link = self._add_link(sock, outbound=True, join=join)
        sock.connected.connect(lambda: self._send_hello(link))
        if join:
            self.log.emit(f"Joining {host}:{port} …")
            sock.connected.connect(lambda: self.log.emit(
                f"Connected to {host}:{port}, waiting for SyncWatch to answer …"))
            link.closed.connect(lambda l: l.peer_id or l.quiet
                                or self.log.emit(self._join_failure(l, host, port)))
        sock.connectToHost(host, port)
        QTimer.singleShot(JOIN_TIMEOUT_MS, lambda: link.peer_id or link.close())

    @staticmethod
    def _join_failure(link: Link, host: str, port: int) -> str:
        if link.error:
            return f"Couldn't join {host}:{port}: {link.error}"
        if link.sock_was_connected:
            return (f"{host}:{port} accepted the connection but didn't answer like "
                    "SyncWatch (wrong port, or a different SyncWatch version?)")
        return (f"No answer from {host}:{port} after {JOIN_TIMEOUT_MS // 1000} s. Check the "
                "address, that SyncWatch is running there, and that the network allows it "
                "(Tailscale connected, share accepted, firewall).")

    def leave(self) -> None:
        for l in list(self.links):
            l.quiet = True
            l.send({"type": "bye"})
            l.sock.flush()
            l.close()
        self.session = uuid.uuid4().hex[:12]
        self.session_label = self.name
        self.peers_changed.emit()

    def broadcast(self, msg: dict, exclude: Link | None = None) -> None:
        for l in self.peers():
            if l is not exclude:
                l.send(msg)

    def send_state(self, state_msg: dict, link: Link | None = None) -> None:
        msg = {"type": "state", **state_msg}
        if link is not None:
            link.send(msg)
        else:
            self.broadcast(msg)

    def local_addresses(self) -> list[str]:
        out = []
        for a in QNetworkInterface.allAddresses():
            if a.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol and not a.isLoopback():
                out.append(a.toString())
        return out

    # -- internals --------------------------------------------------------
    def _add_link(self, sock: QTcpSocket, outbound: bool, join: bool = False) -> Link:
        link = Link(sock, self.peer_id, outbound, join)
        link.message.connect(self._on_message)
        link.closed.connect(self._on_closed)
        self.links.append(link)
        return link

    def _on_incoming(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            link = self._add_link(sock, outbound=False)
            self._send_hello(link)
            QTimer.singleShot(5000, lambda l=link: l.peer_id or l.close())

    def _send_hello(self, link: Link) -> None:
        link.send({"type": "hello", "proto": PROTO, "peer_id": self.peer_id,
                   "name": self.name, "session": self.session,
                   "label": self.session_label, "port": self.port,
                   "join": link.join})

    def _on_closed(self, link: Link) -> None:
        if link in self.links:
            self.links.remove(link)
        if link.peer_id:
            self.log.emit(f"{link.name or 'peer'} disconnected")
            self.peers_changed.emit()

    def _on_message(self, link: Link, msg: dict) -> None:
        t = msg["type"]
        if t == "hello":
            self._on_hello(link, msg)
            return
        if link.peer_id is None:
            return  # nothing before hello
        if t == "state":
            delay = (link.rtt or 0.0) / 2
            self.state_received.emit(msg, delay)
        elif t == "ping":
            link.send({"type": "pong", "t": msg.get("t")})
        elif t == "pong":
            t0 = msg.get("t")
            if isinstance(t0, (int, float)):
                rtt = max(0.0, time.monotonic() - t0)
                if rtt < 10:
                    link.rtt = rtt if link.rtt is None else 0.7 * link.rtt + 0.3 * rtt
                    self.peers_changed.emit()
        elif t == "peers":
            for p in msg.get("peers", []):
                if not isinstance(p, dict):
                    continue
                pid, host, port = p.get("peer_id"), p.get("host"), p.get("port")
                if (isinstance(pid, str) and isinstance(host, str)
                        and isinstance(port, int) and 0 < port < 65536
                        and pid != self.peer_id and not self._linked(pid)
                        and pid > self.peer_id):  # lower id dials; avoids races
                    self.connect_to(host, port, join=False)
        elif t == "status":
            link.status = {k: msg.get(k) for k in ("pos", "drift", "state", "item")}
            if isinstance(msg.get("name"), str) and msg["name"].strip():
                link.name = msg["name"].strip()[:64]
            self.status_received.emit()
        elif t == "bye":
            link.close()

    def _linked(self, peer_id: str) -> bool:
        return any(l.peer_id == peer_id for l in self.links)

    def _on_hello(self, link: Link, msg: dict) -> None:
        pid, session = msg.get("peer_id"), msg.get("session")
        port = msg.get("port")
        if (msg.get("proto") != PROTO or not isinstance(pid, str)
                or not isinstance(session, str) or not isinstance(port, int)
                or link.peer_id is not None):
            link.close()
            return
        if pid == self.peer_id:
            self.log.emit("That address is this instance")
            link.quiet = True
            link.close()
            return
        if link.join:
            # adopt the remote session; drop our own links and state
            for other in [l for l in self.links if l is not link]:
                other.send({"type": "bye"})
                other.close()
            self.session = session
            self.session_label = str(msg.get("label") or msg.get("name") or "")[:64]
            link.join = False
            self.joined.emit()
        elif session != self.session and not msg.get("join"):
            link.close()  # stale address from a different session
            return
        link.peer_id = pid
        link.name = str(msg.get("name") or "peer")[:64]
        link.listen_port = port
        # keep only one link per peer pair: the one opened by the lower id
        dup = [l for l in self.links if l is not link and l.peer_id == pid]
        for d in dup:
            loser = link if link.initiator > d.initiator else d
            loser.close()
            if loser is link:
                return
        self.log.emit(f"{link.name} connected ({link.host})")
        link.send({"type": "ping", "t": time.monotonic()})
        # tell everyone in the session about everyone else
        roster = [{"peer_id": l.peer_id, "host": l.host, "port": l.listen_port}
                  for l in self.peers()]
        self.broadcast({"type": "peers", "peers": roster})
        self.peer_connected.emit(link)
        self.peers_changed.emit()

    def _ping_all(self) -> None:
        self.broadcast({"type": "ping", "t": time.monotonic()})


class Discovery(QObject):
    """Announces our session on the LAN and lists sessions others announce."""

    changed = Signal()

    def __init__(self, mesh: Mesh):
        super().__init__()
        self.mesh = mesh
        self.found: dict[str, dict] = {}   # peer_id -> beacon (+ host, seen)
        self.sock = QUdpSocket(self)
        self.sock.bind(QHostAddress.SpecialAddress.AnyIPv4, DISCOVERY_PORT,
                       QAbstractSocket.BindFlag.ShareAddress
                       | QAbstractSocket.BindFlag.ReuseAddressHint)
        self.sock.readyRead.connect(self._on_read)
        self._timer = QTimer(self, interval=BEACON_INTERVAL_MS, timeout=self._tick)
        self._timer.start()
        QTimer.singleShot(200, self._tick)

    def sessions(self) -> list[dict]:
        """One entry per discovered session other than our own."""
        out: dict[str, dict] = {}
        for b in self.found.values():
            if b["session"] == self.mesh.session:
                continue
            s = out.setdefault(b["session"], {
                "session": b["session"], "label": b["label"],
                "host": b["host"], "port": b["port"], "members": []})
            s["members"].append(b["name"])
        return sorted(out.values(), key=lambda s: s["label"].lower())

    def _tick(self) -> None:
        beacon = json.dumps({
            "app": "syncwatch", "proto": PROTO, "peer_id": self.mesh.peer_id,
            "name": self.mesh.name, "session": self.mesh.session,
            "label": self.mesh.session_label, "port": self.mesh.port,
        }).encode()
        targets = {QHostAddress(QHostAddress.SpecialAddress.Broadcast).toString()}
        for iface in QNetworkInterface.allInterfaces():
            for entry in iface.addressEntries():
                bc = entry.broadcast()
                if not bc.isNull() and bc.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                    targets.add(bc.toString())
        for t in targets:
            self.sock.writeDatagram(beacon, QHostAddress(t), DISCOVERY_PORT)
        cutoff = time.monotonic() - DISCOVERY_TTL
        stale = [k for k, b in self.found.items() if b["seen"] < cutoff]
        for k in stale:
            del self.found[k]
        if stale:
            self.changed.emit()

    def _on_read(self) -> None:
        changed = False
        while self.sock.hasPendingDatagrams():
            dg = self.sock.receiveDatagram(65536)
            try:
                b = json.loads(bytes(dg.data()))
            except (ValueError, UnicodeDecodeError):
                continue
            if (not isinstance(b, dict) or b.get("app") != "syncwatch"
                    or b.get("proto") != PROTO
                    or b.get("peer_id") == self.mesh.peer_id
                    or not isinstance(b.get("peer_id"), str)
                    or not isinstance(b.get("session"), str)
                    or not isinstance(b.get("port"), int)):
                continue
            host = host_str(dg.senderAddress())
            prev = self.found.get(b["peer_id"])
            entry = {"session": b["session"], "label": str(b.get("label") or "")[:64],
                     "name": str(b.get("name") or "peer")[:64], "port": b["port"],
                     "host": host, "seen": time.monotonic()}
            if prev is None or {**prev, "seen": 0} != {**entry, "seen": 0}:
                changed = True
            self.found[b["peer_id"]] = entry
        if changed:
            self.changed.emit()
