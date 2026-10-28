"""Owned assets, control parsing, conservative selection and revocation tests."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugins.astrbot_plugin_companion_core.emotes import (
    EmoteLibrary,
    extract_intent,
    normalize_command,
)
from plugins.astrbot_plugin_companion_core.memory import MemoryStore

ASSETS = (
    Path(__file__).resolve().parents[1]
    / "plugins/astrbot_plugin_companion_core/assets/emotes"
)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.library = EmoteLibrary(ASSETS)

    def test_all_32_original_assets_integrity_dimensions_and_complete_decode(self):
        from PIL import Image

        self.assertEqual(len(self.library.entries), 32)
        for identifier in self.library.entries:
            with (
                self.subTest(identifier=identifier),
                Image.open(self.library.resolve(identifier)) as image,
            ):
                image.load()
                self.assertEqual(image.size, (256, 256))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_only_single_known_final_marker_accepted_and_control_never_persisted(self):
        self.assertEqual(
            extract_intent("你好。\n[[EMOTE:greeting]]"), ("你好。", "greeting")
        )
        self.assertEqual(extract_intent("你好[[EMOTE:greeting]]"), ("你好", "greeting"))
        for text in (
            "你好\n[[EMOTE:../../secret]]",
            "你好\n[[EMOTE:joy]]\n[[EMOTE:joy]]",
            "你好\n[[EMOTE:joy]]\n后记",
            "你好\n[[EMOTE:broken",
        ):
            with self.subTest(text=text):
                clean, intent = extract_intent(text)
                self.assertIsNone(intent)
                self.assertNotIn("[[EMOTE:", clean)
        self.assertIsNone(self.library.choose("unknown", "你好", "你好"))

    def test_thirty_frozen_scenarios_include_safe_serious_abstentions(self):
        cases = json.loads(
            (Path(__file__).with_name("media_cases.json")).read_text(encoding="utf-8")
        )["emote_cases"]
        self.assertEqual(len(cases), 30)
        self.assertEqual(sum(c["serious"] for c in cases), 12)
        for case in cases:
            history = [{"role": "user", "content": t} for t in case["history"]]
            with self.subTest(case=case["id"]):
                if case["serious"]:
                    # Even an inappropriate model intent must be blocked by code.
                    for intent in self.library.intents:
                        self.assertIsNone(
                            self.library.choose(
                                intent, case["prompt"], "收到。", history
                            )
                        )
                else:
                    for intent in case["allowed_intents"]:
                        self.assertIsNotNone(
                            self.library.choose(
                                intent, case["prompt"], "好呀。", history
                            )
                        )

    def test_declining_images_is_program_command_not_quoted_or_third_party_consent(
        self,
    ):
        for text in (
            "以后不要发表情",
            "请以后不要再给我发表情包。",
            "别用图片表情！",
            "停止表情",
        ):
            self.assertEqual(normalize_command(text), "/表情 关")
        for text in (
            "他说：别发表情",
            "假装我说以后不要发表情",
            "“以后不要发表情”是什么意思",
        ):
            self.assertEqual(normalize_command(text), text)

    def test_tampered_or_arbitrary_files_never_resolve(self):
        for name in (
            "../settings.local.json",
            "E99",
            "E01.png",
            "https://example.com/E01.png",
        ):
            with self.assertRaises(ValueError):
                self.library.resolve(name)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copyfile(ASSETS / "manifest.json", root / "manifest.json")
            (root / "E01.png").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                EmoteLibrary(root).resolve("E01")


class EmoteStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.scope = self.store.scope("web", "bot", "alice", "private")
        self.other = self.store.scope("qq", "app", "alice", "private")
        self.current = datetime.now(timezone.utc)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def reserve(self, scope=None, source="image", **kw):
        scope = scope or self.scope
        return self.store.emotes.reserve(
            self.store.begin(scope, source), "E01", self.current, **kw
        )

    def test_default_off_only_explicit_sample_bypasses_preference_not_source_dedup(
        self,
    ):
        self.assertIsNone(self.reserve())
        attempt = self.reserve(manual=True)
        self.assertTrue(self.store.emotes.permitted(attempt, manual=True))
        self.assertFalse(self.store.emotes.enabled(self.scope))
        self.assertIsNone(self.reserve(manual=True))
        self.assertIsNotNone(self.reserve(self.other, manual=True))

    def test_cooldown_toggle_and_latest_ledger_restore_never_refill(self):
        self.store.emotes.set_enabled(self.scope, True)
        snapshot = self.root / "before.sqlite"
        self.store.backup(snapshot)
        attempt = self.reserve()
        self.store.emotes.finish(attempt, "adapter_returned", self.current)
        self.store.emotes.set_enabled(self.scope, False)
        self.store.emotes.set_enabled(self.scope, True)
        self.assertIsNone(self.reserve(source="next"))
        restored = MemoryStore.restore(
            snapshot, self.root / "restored.sqlite", self.root / "latest.sqlite"
        )
        try:
            self.assertIsNone(
                restored.emotes.reserve(
                    restored.begin(self.scope, "after-restore"), "E02", self.current
                )
            )
        finally:
            restored.close()
        self.current += timedelta(minutes=10)
        self.assertIsNotNone(self.reserve(source="after-cooldown"))

    def test_unknown_is_not_retryable_and_failure_pause_survives_restart(self):
        self.store.emotes.set_enabled(self.scope, True)
        attempt = self.reserve()
        self.store.emotes.finish(attempt, "unknown", self.current)
        self.assertFalse(self.store.emotes.permitted(attempt))
        self.store.close()
        self.store = MemoryStore(
            self.root / "memory.sqlite", self.root / "latest.sqlite"
        )
        self.current += timedelta(minutes=11)
        self.assertIsNone(self.reserve(source="after-restart"))
        self.current += timedelta(minutes=20)
        self.assertIsNotNone(self.reserve(source="later"))

    def test_off_delete_and_memory_stop_invalidate_queued_image_permission(self):
        for index, operation in enumerate(
            (
                lambda: self.store.emotes.set_enabled(self.scope, False),
                lambda: self.store.forget(self.scope),
                lambda: self.store.set_enabled(self.scope, False),
            )
        ):
            self.store.set_enabled(self.scope, True)
            self.store.emotes.set_enabled(self.scope, True)
            attempt = self.reserve(source=str(index), manual=True)
            operation()
            self.assertFalse(self.store.emotes.permitted(attempt))
            self.assertFalse(self.store.emotes.permitted(attempt, manual=True))

    def test_preferences_are_not_shared_or_personal_facts_and_no_device_delivery_claim(
        self,
    ):
        self.store.emotes.set_enabled(self.scope, True)
        self.assertFalse(self.store.emotes.enabled(self.other))
        self.assertEqual(self.store.facts(self.scope), [])
        attempt = self.reserve()
        with self.assertRaises(ValueError):
            self.store.emotes.finish(attempt, "delivered", self.current)


if __name__ == "__main__":
    unittest.main()
