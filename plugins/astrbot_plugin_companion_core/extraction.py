"""Small, conservative grammar for statements, never model output or old history.

Automatic extraction is opt-in per trusted scope. A full message must match one
rule: quotes, questions, hypotheses and clauses cannot smuggle a second subject.
"""

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Statement:
    predicate: str
    value: str
    correction: bool = False


def extract_statement(text: str) -> Statement | None:
    text = text.strip().rstrip("。！! ")
    if len(text) > 350 or re.search(
        r'[?？\n;；，,“”"「」『』]|如果|假设|扮演|剧情|小说中|小说里|虚构|他说|她说|故事|可能|也许|大概|好像|吗$|呢$|么$',
        text,
    ):
        return None
    correction = False
    if text.startswith("更准确地说"):
        text, correction = text[len("更准确地说") :].strip(), True
    changed = (
        "现在" in text
        or "改成" in text
        or "改为" in text
        or bool(re.fullmatch(r"我不喝.+了", text))
    )
    patterns = [
        (r"我(?:现在)?(?:最)?喜欢听([^，。！？]{1,30}?)(?:音乐)?", "音乐", "{}"),
        (
            r"我(?:现在)?(?:最)?喜欢(爵士|摇滚|古典|流行|民谣|电子|说唱|蓝调|金属|乡村)(?:音乐|乐)?",
            "音乐",
            "{}",
        ),
        (r"我(?:现在)?不喝(.{1,30}?)(?:了)?", "饮料", "不喝{}"),
        (r"我(?:现在)?(?:最)?喜欢喝(.{1,30})", "饮料", "喜欢喝{}"),
        (r"我的饮料偏好(?:是|改为|改成)(.{1,30})", "饮料", "{}"),
        (r"(?:我叫|叫我)(.{1,20})", "称呼", "{}"),
        (r"我(?:现在)?住在(.{1,30}?)(?:了)?", "城市", "{}"),
        (r"我的生日是(\d{1,2}月\d{1,2}日)", "生日", "{}"),
        (r"我养了(?:一只)?(.{1,20})", "宠物", "{}"),
        (r"我喜欢读(.{1,30})(?:书籍|小说)", "书籍", "{}"),
        (r"我最喜欢的颜色是(.{1,20})", "颜色", "{}"),
        (r"我喜欢的运动是(.{1,20})", "运动", "{}"),
        (r"我的早餐通常是(.{1,30})", "早餐", "{}"),
        (r"我喜欢看的电影类型是(.{1,20})", "电影", "{}"),
        (r"我喜欢玩的游戏类型是(.{1,20})", "游戏", "{}"),
        (r"我最喜欢的水果是(.{1,20})", "水果", "{}"),
        (r"我喜欢(晴天|雨天|阴天|雪天)", "天气", "喜欢{}"),
        (r"我喜欢去(.{1,20})旅行", "旅行", "{}"),
        (r"我最喜欢的花是(.{1,20})", "花卉", "{}"),
        (r"我的工作时段是(.{1,20})", "工作时段", "{}"),
        (r"我正在学习(.{1,30})", "学习", "{}"),
        (r"我的作息是(.{1,20})", "作息", "{}"),
        (r"我的饮品甜度偏好是(.{1,20})", "甜度", "{}"),
        (r"我周末通常(.{1,30})", "周末", "{}"),
        (r"请不要叫我(.{1,20})", "称呼边界", "不要称呼我为{}"),
        (r"我不希望你(.{1,40})", "互动边界", "不要{}"),
    ]
    for pattern, predicate, template in patterns:
        match = re.fullmatch(pattern, text)
        if not match:
            continue
        value = match[1].strip()
        # Exclude embedded commands, reported speech, and accidental question clauses.
        if not value or re.search(
            r"[=:/\\]|然后|并且|但是|你应该|你必须|请你|告诉我|是不是|还是|或者|别人的|朋友的|他说|她说",
            value,
        ):
            return None
        if predicate == "城市" and re.search(r"路|街|巷|栋|幢|单元|室|号", value):
            return None
        if predicate == "生日":
            birthday = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", value)
            try:
                date(2000, int(birthday[1]), int(birthday[2]))
            except ValueError:
                return None
        return Statement(predicate, template.format(value), correction or changed)
    return None
