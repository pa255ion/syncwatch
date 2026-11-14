import random
import unittest

from syncwatch.state import START_DELAY, SessionState

U = "https://www.youtube.com/watch?v="


def sync(*peers: SessionState) -> None:
    """Exchange full snapshots between every pair until nothing changes."""
    changed = True
    while changed:
        changed = False
        for a in peers:
            for b in peers:
                if a is not b and b.merge(a.to_wire()):
                    changed = True


def view(s: SessionState):
    return ([(i.id, i.url, i.title) for i in s.queue()],
            s.play.version, s.play.item, s.play.paused)


class StateTests(unittest.TestCase):
    def test_first_add_starts_playing_after_delay(self):
        a = SessionState("a")
        it = a.add(U + "1")
        self.assertEqual(a.play.item, it.id)
        self.assertFalse(a.play.paused)
        self.assertAlmostEqual(a.play.pos, -START_DELAY)

    def test_concurrent_adds_both_survive(self):
        a, b = SessionState("a"), SessionState("b")
        a.add(U + "a")
        b.add(U + "b")
        sync(a, b)
        self.assertEqual(view(a), view(b))
        self.assertEqual(len(a.queue()), 2)

    def test_remove_wins_over_concurrent_move(self):
        a, b = SessionState("a"), SessionState("b")
        for n in range(3):
            a.add(U + str(n))
        sync(a, b)
        victim = a.queue()[1].id
        a.remove(victim)
        b.move(victim, -1)
        sync(a, b)
        self.assertEqual(view(a), view(b))
        self.assertNotIn(victim, [i.id for i in a.queue()])

    def test_lww_playback(self):
        a, b = SessionState("a"), SessionState("b")
        a.add(U + "1")
        sync(a, b)
        a.set_paused(True)
        b.seek(42)
        sync(a, b)
        self.assertEqual(view(a), view(b))

    def test_position_transfer_adds_one_way_delay(self):
        a, b = SessionState("a"), SessionState("b")
        a.add(U + "1")
        a.seek(100)
        b.merge(a.to_wire(), one_way_delay=0.05)
        self.assertAlmostEqual(b.play.position(), a.play.position() + 0.05, places=2)

    def test_double_end_advances_once(self):
        a, b = SessionState("a"), SessionState("b")
        ids = [a.add(U + str(n)).id for n in range(3)]
        sync(a, b)
        first = a.play.item
        self.assertTrue(a.advance(+1, from_item=first))
        sync(a, b)
        # b's player reports the end of the old video late
        self.assertFalse(b.advance(+1, from_item=first))
        self.assertEqual(b.play.item, ids[1])

    def test_move_and_neighbours(self):
        a = SessionState("a")
        ids = [a.add(U + str(n)).id for n in range(4)]
        a.move(ids[3], -1)
        a.move(ids[3], -1)
        self.assertEqual([i.id for i in a.queue()], [ids[0], ids[3], ids[1], ids[2]])
        a.move(ids[0], +1)
        self.assertEqual([i.id for i in a.queue()], [ids[3], ids[0], ids[1], ids[2]])
        self.assertFalse(a.move(ids[3], -1))

    def test_play_next(self):
        a = SessionState("a")
        ids = [a.add(U + str(n)).id for n in range(3)]
        nxt = a.add(U + "x", play_next=True)
        self.assertEqual([i.id for i in a.queue()], [ids[0], nxt.id, ids[1], ids[2]])

    def test_end_of_queue_then_add_starts_new_item(self):
        a = SessionState("a")
        a.add(U + "1")
        a.advance(+1)
        self.assertIsNone(a.current())
        it = a.add(U + "2")
        self.assertEqual(a.play.item, it.id)

    def test_rejects_non_http_urls_from_peers(self):
        a = SessionState("a")
        a.merge({"items": {"x": {"url": "file:///etc/passwd", "order": 0}},
                 "play": {"v": [5, "z"], "item": "x", "paused": False, "pos": "nan"}})
        self.assertEqual(a.queue(), [])

    def test_random_ops_converge(self):
        rng = random.Random(1)
        peers = [SessionState(p) for p in "abcd"]
        for step in range(400):
            p = rng.choice(peers)
            q = p.queue()
            op = rng.random()
            if op < 0.3 or not q:
                p.add(U + str(step), play_next=rng.random() < 0.3)
            elif op < 0.45:
                p.remove(rng.choice(q).id)
            elif op < 0.6:
                p.move(rng.choice(q).id, rng.choice((-1, 1)))
            elif op < 0.7:
                p.set_paused(rng.random() < 0.5)
            elif op < 0.8:
                p.seek(rng.random() * 100)
            elif op < 0.9:
                p.advance(rng.choice((-1, 1)))
            else:
                p.play_item(rng.choice(q).id)
            if rng.random() < 0.3:  # partial gossip
                x, y = rng.sample(peers, 2)
                y.merge(x.to_wire())
        sync(*peers)
        for p in peers[1:]:
            self.assertEqual(view(p), view(peers[0]))


if __name__ == "__main__":
    unittest.main()
