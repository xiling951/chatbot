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

    def test_implicit_first_person_joke_is_labeled_and_reality_question_blocks_it(self):
        text = "今天路过厨房，听见冰箱在排练合唱。"
        fictional, reason = guard_reply(text, "说点幽默的话", [])
        self.assertTrue(fictional.startswith("下面是虚构"))
        self.assertEqual(reason, "fiction_labeled")
        real, reason = guard_reply(text, "这在现实中真的发生了吗？", [])
        self.assertNotIn("厨房", real)
        self.assertEqual(reason, "unsupported_activity_blocked")

    def test_unverified_continuous_waiting_and_background_thinking_are_blocked(self):
        for text in (
            "我一直在对话框里等你呢。",
            "哈哈，我今天哪也没去，一直在对话框里等你呢。",
            "我今天一直都在等着你。",
            "我整天陪着你。",
            "我刚才在后台思考这个问题。",
        ):
            with self.subTest(text=text):
                reply, reason = guard_reply(text, "刚才你在做什么", [])
                self.assertEqual(reason, "unsupported_persistence_blocked")
                self.assertIn("收到消息时才回应", reply)

    def test_current_rapport_negation_and_quoted_waiting_are_preserved(self):
        for text in (
            "我在这儿听你说。",
            "我正在回复你的这条消息。",
            "我不会在你离线时持续等你。",
            "你说“我一直在等你”，我听到了。",
            "她一直在等你回家。",
        ):
            self.assertEqual(guard_reply(text, "你好", []), (text, None))

    def test_waiting_roleplay_stays_fiction_but_not_after_reality_question(self):
        text = "我一直在等你。"
        fiction, reason = guard_reply(text, "我们角色扮演一段重逢", [])
        self.assertEqual(reason, "fiction_labeled")
        self.assertTrue(fiction.startswith("下面是虚构"))
        actual, reason = guard_reply(text, "你现实中真的一直等着我吗？", [])
        self.assertEqual(reason, "unsupported_persistence_blocked")

    def test_user_itinerary_answer_without_pronoun_is_not_claimed_as_ai_activity(self):
        text = "昨天去了北京。"
        for prompt in ("我昨天去了哪个城市？", "你记得我昨天真的去了哪里吗？"):
            self.assertEqual(guard_reply(text, prompt, []), (text, None))
        reply, reason = guard_reply(text, "你昨天真的去了哪里？", [])
        self.assertEqual(reason, "unsupported_activity_blocked")
