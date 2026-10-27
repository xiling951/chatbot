"""Trusted-scope reminder command interface; never accepts a target from text."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .delivery import DeliveryStore
from .scheduling import daily_slot, utc


def handle_reminder_command(
    text: str,
    core: DeliveryStore,
    *,
    scope: str,
    target: str,
    source: str,
    current: datetime,
    enabled: bool,
    capable: bool,
    private: bool,
    defaults: dict,
) -> str | None:
    if not (
        text in ("/主动状态", "/允许主动", "/停止主动", "/提醒列表", "/提醒帮助")
        or text.startswith(("/提醒 ", "/取消提醒 "))
    ):
        return None
    if not private:
        return "主动提醒仅向已确认的本人私聊开放。"
    if text == "/提醒帮助":
        return "/主动状态\n/允许主动\n/提醒 YYYY-MM-DD HH:MM 内容\n/提醒列表\n/取消提醒 编号\n/停止主动\n使用明确设置的用户时区；当前仅支持文字提醒。总开关、渠道验证、本人授权均通过后才安排。平台接受不代表手机已读。"
    authorization = core.db.execute(
        "SELECT enabled FROM ledger.proactive WHERE scope=?", (scope,)
    ).fetchone()
    if text == "/主动状态":
        counts = {
            r["state"]: r["n"]
            for r in core.db.execute(
                "SELECT state,count(*) AS n FROM outbox WHERE scope=? GROUP BY state",
                (scope,),
            )
        }
        return f"主动总开关：{'开' if enabled else '关'}；渠道验证：{'已验证' if capable else '待验证'}；本会话授权：{'允许' if authorization and authorization[0] else '未允许'}。\n发件状态：{counts or '无'}。平台接受不等于手机已看到或已读。"
    if text == "/停止主动":
        core.authorize(
            scope,
            target,
            False,
            current,
            zone=defaults["timezone"],
            maximum=defaults["maximum_per_local_day"],
            quiet_start=defaults["quiet_start_hour"],
            quiet_end=defaults["quiet_end_hour"],
        )
        return "已阻止后续主动发送并取消排队提醒；已经提交的网络请求或已接受消息可能无法撤回。"
    if text == "/提醒列表":
        rows = core.db.execute(
            "SELECT * FROM tasks WHERE scope=? ORDER BY created_at DESC LIMIT 20",
            (scope,),
        ).fetchall()
        return (
            "\n".join(
                f"{r['id'][:12]} {r['next_run_at'] or '无后续触发'} {r['state']} {r['content']}"
                for r in rows
            )
            or "没有提醒。"
        )
    if text.startswith("/取消提醒 "):
        return (
            "已取消尚未提交的提醒；已提交消息可能无法撤回。"
            if core.cancel_task(scope, text.split(" ", 1)[1].strip())
            else "没有找到本会话的这条提醒。"
        )
    if not enabled or not capable:
        return "主动测试总开关尚未启用，或此渠道尚未验证。没有安排提醒，也没有改变授权；手机验收准备好后再开启。"
    if text == "/允许主动":
        core.authorize(
            scope,
            target,
            True,
            current,
            zone=defaults["timezone"],
            maximum=defaults["maximum_per_local_day"],
            quiet_start=defaults["quiet_start_hour"],
            quiet_end=defaults["quiet_end_hour"],
        )
        return "已允许本会话主动提醒：默认每天最多 2 条，23:00–08:00 免打扰，无回应时降频；用 /停止主动 撤销。"
    policy = core.db.execute(
        "SELECT * FROM proactive_policies WHERE scope=?", (scope,)
    ).fetchone()
    if not authorization or not authorization[0] or not policy:
        return "尚未获得本会话主动授权，请先发 /允许主动。"
    match = re.fullmatch(r"/提醒 (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) (.+)", text)
    if not match:
        return "格式：/提醒 YYYY-MM-DD HH:MM 内容；按已设置的用户时区解释。"
    day = datetime.strptime(match[1], "%Y-%m-%d").date()
    scheduled = daily_slot(day, int(match[2]), int(match[3]), policy["timezone"])
    if scheduled <= utc(current):
        return "提醒时间已经过去，请指定未来时间。"
    content = match[4].strip()
    core.memory._validate_fact("提醒", content)
    grace = defaults["default_grace_seconds"]
    task_id = core.create(
        core.memory.begin(scope, source),
        content,
        scheduled,
        scheduled + timedelta(seconds=grace),
        current,
        key=source,
        grace_seconds=grace,
    )
    local = scheduled.astimezone(ZoneInfo(policy["timezone"])).strftime(
        "%Y-%m-%d %H:%M"
    )
    return f"已安排提醒 {task_id[:12]}：{local}（{policy['timezone']}）。过期、免打扰、撤销或额度规则可能阻止发送；此确认不代表已送达。"
