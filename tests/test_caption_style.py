import unittest

from syncwatch.captions import PRESETS, CaptionStyle, mpv_color, to_mpv


class CaptionStyleTests(unittest.TestCase):
    def test_colour_alpha(self):
        self.assertEqual(mpv_color("#ffcc00", 100), "#FFFFCC00")
        self.assertEqual(mpv_color("#000000", 0), "#00000000")
        self.assertEqual(mpv_color("#000000", 50), "#80000000")

    def test_box_uses_background_box(self):
        m = to_mpv(CaptionStyle(box=True, box_color="#102030", box_opacity=80))
        self.assertEqual(m["sub-border-style"], "background-box")
        self.assertEqual(m["sub-back-color"], "#CC102030")
        self.assertEqual(to_mpv(CaptionStyle())["sub-border-style"], "outline-and-shadow")

    def test_default_font_is_sans(self):
        self.assertEqual(to_mpv(CaptionStyle())["sub-font"], "sans-serif")

    def test_round_trip_and_bad_settings(self):
        s = PRESETS["Yellow, outlined"]
        self.assertEqual(CaptionStyle.from_dict(s.to_dict()), s)
        junk = CaptionStyle.from_dict({"size": "huge", "text_color": "red",
                                       "box_opacity": 999, "margin": -5, "nope": 1})
        self.assertEqual(junk.size, CaptionStyle().size)
        self.assertEqual(junk.text_color, "#FFFFFF")
        self.assertEqual(junk.box_opacity, 100)
        self.assertEqual(junk.margin, 0)
        self.assertEqual(CaptionStyle.from_dict(None), CaptionStyle())


if __name__ == "__main__":
    unittest.main()
