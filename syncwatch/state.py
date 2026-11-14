"""Replicated session state shared by all peers.

Every peer holds a full copy and any peer may change it. Copies converge
because every merge is commutative, associative and idempotent:

* Queue items are keyed by a random id and only ever added. Removal is a
  sticky tombstone; reordering is a last-writer-wins (LWW) register per item.
* Playback (which item, paused, position) is one LWW register.

Versions are ``(lamport_counter, peer_id)`` pairs, so ties break the same
way everywhere.

Positions never use wall-clock time, so peers' clocks need not agree. A
peer anchors the playback position to its own monotonic clock. On the wire
it sends the position *as of the moment it sends*, and the receiver adds
half the measured link round-trip time.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

# Delay given to everyone to load a new video before it starts playing.
START_DELAY = 3.0
MAX_URL_LEN = 2048
MAX_TITLE_LEN = 300


def now() -> float:
    return time.monotonic()


def valid_url(url: object) -> bool:
    return (isinstance(url, str) and len(url) <= MAX_URL_LEN
            and url.startswith(("http://", "https://")))


def _version(v: object) -> tuple[int, str]:
    if (isinstance(v, (list, tuple)) and len(v) == 2
            and isinstance(v[0], int) and isinstance(v[1], str)):
        return (v[0], v[1])
    return (0, "")


@dataclass
class Item:
    id: str
    url: str
    title: str = ""
    order: float = 0.0
    order_v: tuple[int, str] = (0, "")
    removed: bool = False
    added_by: str = ""

    def to_wire(self) -> dict:
        return {"url": self.url, "title": self.title, "order": self.order,
                "order_v": list(self.order_v), "removed": self.removed,
                "added_by": self.added_by}

    @classmethod
    def from_wire(cls, item_id: str, d: dict) -> Item | None:
        if not isinstance(d, dict) or not valid_url(d.get("url")):
            return None
        try:
            order = float(d.get("order", 0.0))
        except (TypeError, ValueError):
            return None
        if order != order or order in (float("inf"), float("-inf")):
            return None
        return cls(id=item_id, url=d["url"],
                   title=str(d.get("title") or "")[:MAX_TITLE_LEN],
                   order=order, order_v=_version(d.get("order_v")),
                   removed=bool(d.get("removed")),
                   added_by=str(d.get("added_by") or "")[:64])


@dataclass
class Playback:
    version: tuple[int, str] = (0, "")
    item: str | None = None
    paused: bool = True
    pos: float = 0.0          # position (s) at ``anchor``
    anchor: float = field(default_factory=now)  # local monotonic time

    def position(self, at: float | None = None) -> float:
        if self.paused:
            return self.pos
        return self.pos + ((now() if at is None else at) - self.anchor)


class SessionState:
    def __init__(self, peer_id: str):
        self.peer_id = peer_id
        self.clock = 0
        self.items: dict[str, Item] = {}
        self.play = Playback()

    # -- versions ---------------------------------------------------------
    def _tick(self) -> tuple[int, str]:
        self.clock += 1
        return (self.clock, self.peer_id)

    def _observe(self, v: tuple[int, str]) -> None:
        self.clock = max(self.clock, v[0])

    # -- queries ----------------------------------------------------------
    def queue(self) -> list[Item]:
        """Live items in play order."""
        return sorted((i for i in self.items.values() if not i.removed),
                      key=lambda i: (i.order, i.id))

    def current(self) -> Item | None:
        it = self.items.get(self.play.item) if self.play.item else None
        return it if it and not it.removed else None

    def _neighbour(self, item_id: str | None, step: int) -> Item | None:
        q = self.queue()
        if item_id is None:
            return q[0] if q and step > 0 else None
        ref = self.items.get(item_id)
        if ref is None:
            return None
        key = (ref.order, ref.id)
        if step > 0:
            later = [i for i in q if (i.order, i.id) > key]
            return later[0] if later else None
        earlier = [i for i in q if (i.order, i.id) < key]
        return earlier[-1] if earlier else None

    # -- local mutations (each returns True if something changed) ---------
    def add(self, url: str, title: str = "", added_by: str = "",
            play_next: bool = False) -> Item:
        q = self.queue()
        if play_next and self.current() is not None:
            cur = self.current()
            nxt = self._neighbour(cur.id, +1)
            order = (cur.order + nxt.order) / 2 if nxt else cur.order + 1
        else:
            order = (q[-1].order + 1) if q else 0.0
        item = Item(id=uuid.uuid4().hex[:16], url=url, title=title[:MAX_TITLE_LEN],
                    order=order, order_v=self._tick(), added_by=added_by[:64])
        self.items[item.id] = item
        if self.current() is None:  # idle: start playing what was just added
            self.set_playback(item.id, paused=False, pos=-START_DELAY)
        return item

    def set_title(self, item_id: str, title: str) -> bool:
        it = self.items.get(item_id)
        if it is None or it.title or not title:
            return False
        it.title = title[:MAX_TITLE_LEN]
        return True

    def remove(self, item_id: str) -> bool:
        it = self.items.get(item_id)
        if it is None or it.removed:
            return False
        if self.play.item == item_id:
            self.advance(+1)
        it.removed = True
        return True

    def move(self, item_id: str, step: int) -> bool:
        it = self.items.get(item_id)
        if it is None or it.removed:
            return False
        q = self.queue()
        idx = q.index(it)
        target = idx + step
        if not 0 <= target < len(q):
            return False
        # place between the target's neighbours on the far side
        if step < 0:
            lo = q[target - 1].order if target > 0 else q[target].order - 2
            hi = q[target].order
        else:
            lo = q[target].order
            hi = q[target + 1].order if target + 1 < len(q) else q[target].order + 2
        it.order = (lo + hi) / 2
        it.order_v = self._tick()
        return True

    def set_playback(self, item_id: str | None, paused: bool, pos: float) -> None:
        self.play = Playback(version=self._tick(), item=item_id,
                             paused=paused, pos=pos, anchor=now())

    def play_item(self, item_id: str) -> bool:
        if item_id not in self.items or self.items[item_id].removed:
            return False
        self.set_playback(item_id, paused=False, pos=-START_DELAY)
        return True

    def set_paused(self, paused: bool) -> bool:
        if self.current() is None or self.play.paused == paused:
            return False
        pos = max(0.0, self.play.position())
        self.set_playback(self.play.item, paused=paused, pos=pos)
        return True

    def seek(self, pos: float) -> bool:
        if self.current() is None:
            return False
        self.set_playback(self.play.item, paused=self.play.paused, pos=max(0.0, pos))
        return True

    def advance(self, step: int, from_item: str | None = None) -> bool:
        """Go to the next/previous item. ``from_item`` guards against two
        peers both reporting the end of the same video: the advance only
        happens if that video is still the current one."""
        if from_item is not None and from_item != self.play.item:
            return False
        nxt = self._neighbour(self.play.item, step)
        if nxt is None:
            if step > 0 and self.play.item is not None:
                self.set_playback(None, paused=True, pos=0.0)  # ran off the end
                return True
            return False
        self.set_playback(nxt.id, paused=False, pos=-START_DELAY)
        return True

    def clear_played(self) -> bool:
        cur = self.current()
        if cur is None:
            return False
        changed = False
        for it in self.queue():
            if (it.order, it.id) < (cur.order, cur.id):
                it.removed = True
                changed = True
        return changed

    def reset(self) -> None:
        self.items.clear()
        self.play = Playback()

    # -- replication ------------------------------------------------------
    def to_wire(self) -> dict:
        p = self.play
        return {
            "clock": self.clock,
            "items": {k: v.to_wire() for k, v in self.items.items()},
            "play": {"v": list(p.version), "item": p.item, "paused": p.paused,
                     "pos": p.position() if p.item else p.pos},
        }

    def merge(self, msg: dict, one_way_delay: float = 0.0) -> bool:
        """Merge a remote snapshot. Returns True if local state changed."""
        changed = False
        clock = msg.get("clock")
        if isinstance(clock, int):
            self.clock = max(self.clock, clock)

        items = msg.get("items")
        if isinstance(items, dict):
            for item_id, d in items.items():
                if not isinstance(item_id, str) or len(item_id) > 64:
                    continue
                remote = Item.from_wire(item_id, d)
                if remote is None:
                    continue
                self._observe(remote.order_v)
                local = self.items.get(item_id)
                if local is None:
                    self.items[item_id] = remote
                    changed = True
                    continue
                if remote.removed and not local.removed:
                    local.removed = True
                    changed = True
                if remote.order_v > local.order_v:
                    local.order, local.order_v = remote.order, remote.order_v
                    changed = True
                if remote.title and not local.title:
                    local.title = remote.title
                    changed = True

        play = msg.get("play")
        if isinstance(play, dict):
            v = _version(play.get("v"))
            self._observe(v)
            if v > self.play.version:
                item = play.get("item")
                if not (item is None or isinstance(item, str)):
                    return changed
                try:
                    pos = float(play.get("pos", 0.0))
                except (TypeError, ValueError):
                    return changed
                if pos != pos or abs(pos) > 1e7:
                    return changed
                paused = bool(play.get("paused", True))
                if not paused:
                    pos += one_way_delay
                self.play = Playback(version=v, item=item, paused=paused,
                                     pos=pos, anchor=now())
                changed = True
        return changed
