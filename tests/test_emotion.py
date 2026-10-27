import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from plugins.astrbot_plugin_companion_core.emotion import EmotionState
from plugins.astrbot_plugin_companion_core.memory import MemoryStore
from plugins.astrbot_plugin_companion_core.scheduling import daily_slot


class EmotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.memory = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.scope = self.memory.scope("web", "bot", "alice", "private")
        self.other = self.memory.scope("web", "bot", "bob", "private")
        self.core = EmotionState(self.memory)
        self.current = daily_slot(date(2026, 10, 26), 12, 0, "America/Tijuana")

    def tearDown(self):
        self.memory.close()
        self.temp.cleanup()

    def observe(self, text, source="message"):
        return self.core.observe(
            self.memory.begin(self.scope, source), text, self.current
        )

    def test_reason_specific_state_changes_tone_and_is_not_a_fact(self):
        self.assertEqual(self.observe("我今天有点累。"), "supportive")
        self.assertIn("平静陪伴", self.core.style(self.scope, self.current))
        self.assertEqual(self.memory.facts(self.scope), [])
        self.assertIsNone(self.core.read(self.other, self.current))
        self.assertEqual(
            self.memory.db.execute("SELECT count(*) FROM ledger.proactive").fetchone()[
                0
            ],
            0,
        )

    def test_neutral_chats_and_absence_do_not_raise_attachment_or_create_events(self):
        for index in range(20):
            self.assertIsNone(self.observe("你好", str(index)))
        self.assertIsNone(self.core.read(self.scope, self.current + timedelta(days=7)))
        self.assertEqual(
            self.memory.db.execute("SELECT count(*) FROM emotion_events").fetchone()[0],
            0,
        )

    def test_duplicate_source_is_idempotent(self):
        self.observe("谢谢你听我说。")
        first = self.core.read(self.scope, self.current)
        self.assertIsNone(self.observe("谢谢你听我说。"))
        self.assertEqual(self.core.read(self.scope, self.current), first)

    def test_decay_returns_towards_baseline_without_punishment(self):
        self.observe("我想安静会儿。")
        first = self.core.read(self.scope, self.current)
        self.assertIn("安静、简短", self.core.style(self.scope, self.current))
        later = self.core.read(self.scope, self.current + timedelta(hours=6))
        self.assertAlmostEqual(later["arousal"], 0.3 + (first["arousal"] - 0.3) / 2)
        self.assertGreater(later["social_energy"], first["social_energy"])

    def test_restart_persists_state_and_sources(self):
        self.observe("我很开心")
        first = self.core.read(self.scope, self.current)
        self.memory.close()
        self.memory = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.core = EmotionState(self.memory)
        self.assertEqual(self.core.read(self.scope, self.current), first)
        self.assertEqual(
            self.memory.db.execute("SELECT reason FROM emotion_events").fetchone()[0],
            "celebrate",
        )

    def test_delete_stop_and_old_snapshot_cannot_revive_state(self):
        self.observe("我很难过")
        snapshot = self.root / "old.sqlite"
        self.memory.backup(snapshot)
        ticket = self.memory.begin(self.scope, "late")
        self.memory.forget(self.scope)
        with self.assertRaises(ValueError):
            self.core.observe(ticket, "我很开心", self.current)
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertIsNone(EmotionState(restored).read(self.scope, self.current))
            self.assertEqual(
                restored.db.execute("SELECT count(*) FROM emotion_events").fetchone()[
                    0
                ],
                0,
            )
        finally:
            restored.close()
        self.observe("我很开心", "fresh")
        self.memory.set_enabled(self.scope, False)
        self.assertEqual(self.core.style(self.scope, self.current), "")

    def test_roleplay_quotes_and_questions_do_not_change_real_interaction_state(self):
        for text in [
            "如果我很开心",
            "他说我很开心",
            "角色扮演：我很开心",
            "我很开心？",
            "“我很开心”",
        ]:
            self.assertIsNone(self.observe(text))
        self.assertIsNone(self.core.read(self.scope, self.current))

    def test_state_is_bounded_and_rejects_invalid_decay_policy(self):
        for index in range(20):
            self.observe("我很开心", str(index))
        state = self.core.read(self.scope, self.current)
        self.assertTrue(all(0 <= v <= 1 for v in state.values()))
        with self.assertRaises(ValueError):
            EmotionState(self.memory, {"half_life_hours": 0})
