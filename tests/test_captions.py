"""Caption track choice: real tracks beat YouTube machine translations."""
import unittest

from syncwatch.player import Player

YT = "edl://!no_clip;!delay_open,media_type=sub;https://www.youtube.com/api/timedtext?v=x"


def track(tid, lang, url):
    return {"id": tid, "type": "sub", "lang": lang, "external-filename": url}


def pick(lang, tracks):
    p = Player.__new__(Player)  # ranking needs no mpv instance
    p.caption_lang = lang
    ranked = sorted((r, t) for t in tracks if (r := p._caption_rank(t)) is not None)
    return ranked[0][1]["id"] if ranked else None


class CaptionChoiceTests(unittest.TestCase):
    def test_prefers_untranslated_auto_over_translation(self):
        # the Korean video: "en" is a translation, "en-US" is YouTube's own ASR
        tracks = [track(1, "en", YT + "&kind=asr&lang=en-US&tlang=en"),
                  track(5, "en-ko", YT + "&kind=asr&lang=ko&tlang=en"),
                  track(6, "en-US", YT + "&kind=asr&lang=en-US")]
        self.assertEqual(pick("en", tracks), 6)

    def test_prefers_uploaded_over_auto(self):
        tracks = [track(1, "en", YT + "&kind=asr&lang=en"),
                  track(2, "en", YT + "&lang=en")]
        self.assertEqual(pick("en", tracks), 2)

    def test_falls_back_to_translation(self):
        tracks = [track(1, "ko", YT + "&kind=asr&lang=ko"),
                  track(2, "en", YT + "&kind=asr&lang=ko&tlang=en")]
        self.assertEqual(pick("en", tracks), 2)

    def test_no_match(self):
        self.assertIsNone(pick("ja", [track(1, "en", YT + "&lang=en")]))


if __name__ == "__main__":
    unittest.main()
