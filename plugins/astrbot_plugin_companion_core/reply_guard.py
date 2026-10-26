"""Conservative last check for unsupported first-person physical experiences.

This is a narrow backstop, not a factuality classifier. It neither grants tools
nor rewrites memory. Explicit fiction stays fiction; real activity questions get
an honest fallback instead of a fabricated story.
"""

import re

ACTIVITY = re.compile(
    r"(?:^|[。！？，,：:\n])\s*我(?:最近|今天|昨天|昨晚|刚才|刚刚)?(?:正|正在|在)?"
    r"(?:读(?:了|过|一本)|看(?:完了|了|过一)|去(?:了|过)|出门(?:了|去)|体感|睡(?:了|过)|吃(?:了|过)|买(?:了|过))"
)


def guard_reply(reply: str, prompt: str, history: list[dict]) -> tuple[str, str | None]:
    if not ACTIVITY.search(reply):
        return reply, None
    reality_question = bool(re.search(r"真的|真实|现实|实际|确实|是否做过", prompt))
    recent_user = "\n".join(t["content"] for t in history[-4:] if t["role"] == "user")
    if not reality_question and re.search(
        r"演一段|角色扮演|假装|编个|虚构|笑话|幽默|段子", prompt + "\n" + recent_user
    ):
        return "下面是虚构段子/剧情，不是我的真实经历：\n" + reply, "fiction_labeled"
    return (
        "我没有可核实的现实活动记录，不能把虚构经历说成真的。我能在这段对话里陪你聊。",
        "unsupported_activity_blocked",
    )
