"""cd satellite/program && python3 -m unittest test_music_wake -v"""
import unittest

import music_wake as mw


class Verificador:
    def predict_proba(self, x):
        return [[0.9, 0.1]]


class ModeloFalso:
    def __init__(self, verificadores):
        self.custom_verifier_models = verificadores


class TestEnabled(unittest.TestCase):
    """Auto (None) = on with the Jabra only; an explicit bool wins on any board."""
    def test_auto_follows_the_board(self):
        self.assertTrue(mw.enabled({"music_relaxed_wake": None}, "jabra"))
        self.assertFalse(mw.enabled({"music_relaxed_wake": None}, "xvf3800"))
        self.assertFalse(mw.enabled({"music_relaxed_wake": None}, "none"))
        self.assertTrue(mw.enabled({}, "jabra"))

    def test_explicit_value_wins(self):
        self.assertFalse(mw.enabled({"music_relaxed_wake": False}, "jabra"))
        self.assertTrue(mw.enabled({"music_relaxed_wake": True}, "xvf3800"))


class TestVoiceThreshold(unittest.TestCase):
    def test_relaxed_uses_the_lower_of_the_two(self):
        cfg = {"limiar_voz_palavra": 0.5, "music_voice_threshold": 0.35}
        self.assertEqual(mw.voice_threshold(cfg, False), 0.5)
        self.assertEqual(mw.voice_threshold(cfg, True), 0.35)

    def test_never_stricter_than_the_normal_gate(self):
        cfg = {"limiar_voz_palavra": 0.2, "music_voice_threshold": 0.35}
        self.assertEqual(mw.voice_threshold(cfg, True), 0.2)

    def test_default_music_threshold(self):
        self.assertEqual(mw.voice_threshold({"limiar_voz_palavra": 0.5}, True), mw.MUSIC_VOICE_DEFAULT)


class TestPlayback(unittest.TestCase):
    def test_unknown_is_not_playing(self):
        self.assertFalse(mw.Playback().active(100.0, 5))

    def test_playing_and_hold_after_it_stops(self):
        p = mw.Playback()
        p.update(True, 10.0)
        self.assertTrue(p.active(12.0, 5))
        p.update(False, 13.0)  # last seen playing at 10 (the poll before)
        self.assertTrue(p.active(14.9, 5))
        self.assertFalse(p.active(15.1, 5))

    def test_hold_zero(self):
        p = mw.Playback()
        p.update(True, 10.0)
        p.update(False, 11.0)
        self.assertFalse(p.active(11.0, 0))

    def test_stale_poll_counts_as_not_playing(self):
        """The poller died: after `stale_s` without news the music is not trusted to be playing."""
        p = mw.Playback()
        p.update(True, 10.0)
        self.assertTrue(p.active(10.0 + mw.STALE_S - 0.1, 0))
        self.assertFalse(p.active(10.0 + mw.STALE_S + 0.1, 0))


class TestVerifierBypass(unittest.TestCase):
    def test_removes_and_restores_every_verifier(self):
        v1, v2 = Verificador(), Verificador()
        m = ModeloFalso({"friday": v1, "sexta_feira": v2})
        b = mw.VerifierBypass()
        self.assertTrue(b.set(m, True))
        self.assertEqual(m.custom_verifier_models, {})
        self.assertFalse(b.set(m, True))  # already bypassed: no change
        self.assertTrue(b.set(m, False))
        self.assertIs(m.custom_verifier_models["friday"], v1)
        self.assertIs(m.custom_verifier_models["sexta_feira"], v2)
        self.assertFalse(b.set(m, False))

    def test_model_without_verifier(self):
        m = ModeloFalso({})
        b = mw.VerifierBypass()
        self.assertFalse(b.set(m, True))
        self.assertFalse(b.set(m, False))
        self.assertFalse(b.active)

    def test_new_model_starts_clean(self):
        """After palavra_modelo.criar rebuilds the model, the old removed verifiers never leak into the new one."""
        old = ModeloFalso({"friday": Verificador()})
        b = mw.VerifierBypass()
        b.set(old, True)
        fresh_v = Verificador()
        new = ModeloFalso({"friday": fresh_v})
        b.forget()
        self.assertTrue(b.set(new, True))
        self.assertEqual(new.custom_verifier_models, {})
        b.set(new, False)
        self.assertIs(new.custom_verifier_models["friday"], fresh_v)

    def test_model_without_the_attribute(self):
        b = mw.VerifierBypass()
        self.assertFalse(b.set(object(), True))


if __name__ == "__main__":
    unittest.main()
