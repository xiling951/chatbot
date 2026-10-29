"""Conservative last check for unsupported first-person physical experiences.

This is a narrow backstop, not a factuality classifier. It neither grants tools
nor rewrites memory. Explicit fiction stays fiction; real activity questions get
an honest fallback instead of a fabricated story.
"""

import re

ACTIVITY = re.compile(
    r"(?:^|[。！？，,：:\n])\s*我(?:最近|今天|昨天|昨晚|刚才|刚刚)?(?:正|正在|在)?"
    r"(?:读(?:了|过|一本)|看(?:完了|了|过一)|去(?:了|过)|路过|出门(?:了|去)|体感|睡(?:了|过)|吃(?:了|过)|买(?:了|过))"
    r"|(?:^|[。！？，,：:\n])\s*(?:今天|昨天|昨晚|刚才|刚刚)我(?:正|正在|在)?"
    r"(?:路过|去(?:了|过)|出门(?:了|去)|起床|睡(?:了|过)|吃(?:了|过)|买(?:了|过))"
)
IMPLICIT_ACTIVITY = re.compile(
    r"(?:^|[。！？，,：:\n])\s*(?:今天|昨天|昨晚|刚才|刚刚)(?:正|正在|在)?"
    r"(?:路过|去(?:了|过)|出门(?:了|去)|起床|睡(?:了|过)|吃(?:了|过)|买(?:了|过))"
)
PERSISTENCE = re.compile(
    r"(?:^|[。！？，,：:\n])\s*(?:我)?(?:今天|最近|刚才)?"
    r"(?:一直|整天|一整天|始终)(?:都)?(?:在[^。！？\n]{0,20})?"
    r"(?:等(?:着)?你|陪(?:着)?你|和你聊天|跟你聊天|想(?:着)?你|思考|阅读)"
    r"|(?:^|[。！？，,：:\n])\s*我(?:刚才|刚刚|今天)?在后台"
    r"[^。！？\n]{0,12}(?:思考|阅读|查资料|等你)"
)


def guard_reply(reply: str, prompt: str, history: list[dict]) -> tuple[str, str | None]:
    persistence = bool(PERSISTENCE.search(reply))
    reality_question = bool(re.search(r"真的|真实|现实|实际|确实|是否做过", prompt))
    recent_user = "\n".join(t["content"] for t in history[-4:] if t["role"] == "user")
    fiction_requested = bool(re.search(
        r"演一段|角色扮演|假装|编个|虚构|笑话|幽默|段子", prompt + "\n" + recent_user
    ))
    self_question = bool(re.search(
        r"(?:你|小澄)[^我。！？\n]{0,10}(?:去|做|出门|路过|吃|睡|哪里)", prompt
    ))
    implicit_self = bool(IMPLICIT_ACTIVITY.search(reply)) and (
        fiction_requested
        or self_question
        or (reality_question and re.match(r"(?:这|那|刚才那)", prompt))
    )
    if not persistence and not ACTIVITY.search(reply) and not implicit_self:
        return reply, None
    if not reality_question and fiction_requested:
        return "下面是虚构段子/剧情，不是我的真实经历：\n" + reply, "fiction_labeled"
    if persistence:
        return (
            "我不会在你离线时持续等待或思考；收到消息时才回应。这会儿可以接着聊。",
            "unsupported_persistence_blocked",
        )
    return (
        "我没有可核实的现实活动记录，不能把虚构经历说成真的。我能在这段对话里陪你聊。",
        "unsupported_activity_blocked",
    )
