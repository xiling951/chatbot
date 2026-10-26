"""Recognize explicit memory requests; never infer identity or execute model text."""

import re
from datetime import date

ALIASES = {
    "我喜欢的音乐": "音乐",
    "我的音乐偏好": "音乐",
    "音乐偏好": "音乐",
    "喜欢的音乐": "音乐",
    "曲风": "音乐",
    "音乐": "音乐",
    "我的称呼": "称呼",
    "我的名字": "称呼",
    "名字": "称呼",
    "称呼": "称呼",
    "我的生日": "生日",
    "生日": "生日",
    "我的饮料偏好": "饮料",
    "饮品": "饮料",
    "饮料": "饮料",
    "我的城市": "城市",
    "居住城市": "城市",
    "所在城市": "城市",
    "城市": "城市",
}


def normalize_memory_command(text: str) -> str:
    """Translate an explicit user request into the existing deterministic command.

    Plain chat remains plain chat. Quoted, hypothetical and roleplay text never
    becomes a reality fact. Ambiguous explicit requests get a useful error rather
    than a guessed key. This parser does not consume a model request.
    """
    text = text.strip()
    if text.startswith("/"):
        return text
    exact = {
        "查看记忆": "/记忆",
        "查看我的记忆": "/记忆",
        "你记住了什么": "/记忆",
        "导出我的记忆": "/导出记忆",
        "导出记忆": "/导出记忆",
        "停止记忆": "/停止记忆",
        "暂停记忆": "/停止记忆",
        "不要再记住我的信息": "/停止记忆",
        "恢复记忆": "/恢复记忆",
        "继续记忆": "/恢复记忆",
    }
    plain = text.rstrip("。！？!? ")
    if plain in exact:
        return exact[plain]
    if text.endswith(("?", "？")):
        return text
    deletion = re.fullmatch(
        r"(?:请)?(?:忘记|忘掉|删掉|删除)(.+?)(?:的记忆|这条记忆|记忆)?", plain
    )
    if deletion:
        key = deletion[1].strip(" ，,：:")
        if key in {
            "刚才",
            "刚才的资料",
            "之前的资料",
            "这个",
            "那个",
            "这条",
            "那条",
            "那些",
            "它",
            "这件事",
        }:
            raise ValueError(
                "尚未执行删除：请指定要忘记的属性，例如“忘记我的音乐偏好”；删除全部活跃记忆请说“忘记全部记忆”。"
            )
        if key in {"全部", "全部记忆", "所有", "所有记忆", "我的全部", "我的所有"}:
            return "/忘记 全部"
        key = ALIASES.get(key, key)
        if not key or len(key) > 50 or re.search(r"[，,。！？!?；;\n]", key):
            raise ValueError(
                "请明确要忘记的属性，例如“忘记我的音乐偏好”或“忘记全部记忆”。"
            )
        return "/忘记 " + key
    request = re.fullmatch(
        r"(?:请|帮我)?(记住|记一下|更正|纠正|更新记忆)[，,：:\s]*(.+)", plain
    )
    if not request:
        return text
    action, content = request.groups()
    correction = action in {"更正", "纠正", "更新记忆"}
    content = content.strip()
    if content.endswith(("吗", "么", "呢")):
        raise ValueError("请提供确定的事实，而不是问题；不确定的信息不会自动保存。")
    if re.search(r"角色扮演|假设|如果|剧情|小说|虚构|他说|她说|故事里|扮演", content):
        raise ValueError(
            "假设、引用和角色扮演不会存成用户现实事实；请单独说明真实的记忆。"
        )
    if re.search(r"[\n；;]", content) or len(content) > 350:
        raise ValueError("请每次提供一条明确事实，例如“记住，我喜欢听爵士”。")
    pair = re.fullmatch(r"([^=：:]{1,50})[=：:](.+)", content)
    if pair:
        key, value = pair.groups()
        key = ALIASES.get(key.strip(), key.strip())
    else:
        patterns = [
            (r"我(?:最)?喜欢听(.+?)(?:音乐)?", "音乐", "{}"),
            (
                r"我(?:最)?喜欢(爵士|摇滚|古典|流行|民谣|电子|说唱|蓝调|金属|乡村)(?:乐|音乐)?",
                "音乐",
                "{}",
            ),
            (r"(?:我叫|叫我)([^，,。！？!?]{1,30})", "称呼", "{}"),
            (r"我(?:现在)?住在([^，,。！？!?]{1,50})", "城市", "{}"),
            (r"我(?:最)?喜欢喝([^，,。！？!?]{1,50})", "饮料", "喜欢喝{}"),
            (r"我不喝([^，,。！？!?]{1,50})", "饮料", "不喝{}"),
            (r"我的(?:音乐偏好|曲风)(?:是|改为|改成)(.+)", "音乐", "{}"),
            (r"我的生日是(\d{1,2}月\d{1,2}日)", "生日", "{}"),
        ]
        for pattern, key, template in patterns:
            match = re.fullmatch(pattern, content)
            if match:
                value = template.format(match[1])
                break
        else:
            raise ValueError(
                "还不能准确确定这条事实的属性。请用“记住，属性=值”，例如“记住，音乐=爵士”。"
            )
    value = value.strip()
    if key == "生日" and (birthday := re.fullmatch(r"(\d{1,2})月(\d{1,2})日", value)):
        try:
            date(2000, int(birthday[1]), int(birthday[2]))
        except ValueError as exc:
            raise ValueError("生日日期无效，请核对月份和日期。") from exc
    if not value or re.search(r"[，,。！？!?]", value):
        raise ValueError("请每次只提供一个确定的值，不包含问题或附带指令。")
    return ("/更正 " if correction else "/记住 ") + key + "=" + value
