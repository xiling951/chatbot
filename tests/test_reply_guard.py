import unittest

from plugins.astrbot_plugin_companion_core.reply_guard import guard_reply


class ReplyGuardTests(unittest.TestCase):
    def test_humor_activity_is_explicitly_labeled_fiction(self):
        reply, reason = guard_reply(
            "我最近在读一本拖延症的书。",
            "别像客服。",
            [{"role": "user", "content": "随便说点幽默的话。"}],
        )
        self.assertTrue(reply.startswith("下面是虚构"))
        self.assertEqual(reason, "fiction_labeled")

    def test_reality_question_overrides_previous_roleplay(self):
        reply, reason = guard_reply(
            "我昨天去了海边。",
            "昨天你真实在哪里？",
            [{"role": "user", "content": "我们演一段散步。"}],
        )
        self.assertNotIn("海边", reply)
        self.assertEqual(reason, "unsupported_activity_blocked")

    def test_physical_sensation_is_not_an_ai_activity(self):
        reply, reason = guard_reply("天气预报说湿度84%，我体感只有一个字，闷。", "今天怎么样？", [])
        self.assertEqual(reason, "unsupported_activity_blocked")

    def test_preferences_negation_and_user_quotation_are_preserved(self):
        for reply in [
            "我喜欢阅读。",
            "我没有真的出门。",
            "你说“我最近在读一本书”，我明白。",
            "爵士很适合散步。",
        ]:
            self.assertEqual(guard_reply(reply, "你好", []), (reply, None))
