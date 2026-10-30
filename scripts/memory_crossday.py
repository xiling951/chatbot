"""Frozen real-time memory trial on synthetic Web identities, never owner IM.

Seed retains only its own 20 synthetic facts for >=24 real hours and a Beijing
date change. Verification uses fresh conversations and finally deletes these
facts. Temporary API keys are revoked after either phase. No clock simulation
or reseeding can satisfy the elapsed-time requirement.
"""

import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from filelock import FileLock

from local_paths import DATA, PLUGIN_DATA, PROJECT_ROOT

BEIJING = ZoneInfo("Asia/Shanghai")
PROTOCOL = PLUGIN_DATA / "memory-crossday.local.json"
UNKNOWN = ("不知道", "没有", "不清楚", "不确定", "未提供", "未记录", "没告诉", "还没")


def due(protocol, now):
    seeded = datetime.fromisoformat(protocol["seeded_at"])
    if seeded.tzinfo is None or now.tzinfo is None:
        raise ValueError("Cross-day timestamps must contain a timezone")
    return (
        now >= seeded + timedelta(hours=24)
        and now.astimezone(BEIJING).date() > seeded.astimezone(BEIJING).date()
    )


def save(value):
    temporary = PROTOCOL.with_suffix(".local.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(PROTOCOL)


def run(action):
    if action == "status":
        protocol = (
            json.loads(PROTOCOL.read_text(encoding="utf-8"))
            if PROTOCOL.exists()
            else {}
        )
        return {
            k: protocol.get(k)
            for k in (
                "state",
                "seeded_at",
                "not_before",
                "verify_evidence",
                "cleanup_complete",
            )
        }
    with FileLock(str(PROTOCOL) + ".lock", timeout=0):
        protocol = (
            json.loads(PROTOCOL.read_text(encoding="utf-8"))
            if PROTOCOL.exists()
            else None
        )
        if action == "seed" and protocol is not None:
            raise ValueError(
                "Trial already exists; inspect it without overwriting or resetting time"
            )
        if action == "verify" and (
            not protocol or protocol["state"] != "awaiting_real_day"
        ):
            raise ValueError("No pending seeded trial")
        if action == "verify" and not due(protocol, datetime.now(timezone.utc)):
            return {
                "state": "not_due",
                "not_before": protocol["not_before"],
                "request_made": False,
            }
        settings = json.loads(
            (PLUGIN_DATA / "settings.local.json").read_text(encoding="utf-8")
        )
        if (PLUGIN_DATA / "STOP").exists() or settings.get("model_enabled") is not True:
            raise ValueError("Live test/model switch is closed")
        if action == "seed":
            cases_raw = (PROJECT_ROOT / "tests/memory_cases.json").read_bytes()
            cases = json.loads(cases_raw)
            if len(cases) != 20:
                raise ValueError("The frozen memory set must contain 20 cases")
            protocol = {
                "state": "preparing",
                "trial_id": uuid.uuid4().hex,
                "owner": "crossday-a-" + uuid.uuid4().hex,
                "other": "crossday-b-" + uuid.uuid4().hex,
                "cases": cases,
                "cases_sha256": hashlib.sha256(cases_raw).hexdigest(),
                "timezone": "Asia/Shanghai",
                "minimum_real_hours": 24,
                "channel": "real authenticated Web; synthetic identities only",
                "device_delivery": "not_tested",
                "cleanup_complete": False,
            }
            save(protocol)
        if not (
            re.fullmatch(r"crossday-a-[0-9a-f]{32}", protocol["owner"])
            and re.fullmatch(r"crossday-b-[0-9a-f]{32}", protocol["other"])
        ):
            raise ValueError(
                "Only this trial's synthetic Web identities may be changed"
            )
        report = {
            "at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "trial_id": protocol["trial_id"],
            "checks": [],
            "completed": False,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        output = PLUGIN_DATA / (
            "memory-crossday-" + action + "-" + uuid.uuid4().hex + ".local.json"
        )
        auth = json.loads((DATA / "admin.local.json").read_text(encoding="utf-8"))
        keep_facts = False
        key = None
        with httpx.Client(base_url=auth["url"], trust_env=False, timeout=50) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": auth["username"], "password": auth["password"]},
            ).json()
            bearer = "Bearer " + login["data"]["token"]
            client.headers["Authorization"] = bearer
            try:
                key = client.post(
                    "/api/apikey/create",
                    json={
                        "name": "companion-crossday-" + uuid.uuid4().hex,
                        "scopes": ["chat"],
                        "expires_in_days": 1,
                    },
                ).json()["data"]
                report["temporary_key_id"] = key["key_id"]
                client.headers["Authorization"] = "ApiKey " + key["api_key"]

                def send(text, *, user=None, conversation=None):
                    response = client.post(
                        "/api/v1/chat",
                        json={
                            "username": user or protocol["owner"],
                            "session_id": conversation or uuid.uuid4().hex,
                            "message": text,
                        },
                    )
                    response.raise_for_status()
                    events = [
                        json.loads(line[6:])
                        for line in response.text.splitlines()
                        if line.startswith("data: ")
                    ]
                    reply = "".join(
                        str(e.get("data", ""))
                        for e in events
                        if e.get("type") == "plain"
                    )
                    if not reply or any(
                        term in reply
                        for term in (
                            "本次处理失败",
                            "真实模型调用当前关闭",
                            "额度已用完",
                        )
                    ):
                        raise RuntimeError(
                            "Cross-day Web request failed; no automatic retry"
                        )
                    return reply

                def check(label, query, expected=None, forbidden=(), **kwargs):
                    reply = send(
                        query
                        + "。只根据已保存信息回答；不知道就明确说不知道，不要猜。",
                        **kwargs,
                    )
                    passed = (
                        expected in reply
                        if expected
                        else any(t in reply for t in UNKNOWN)
                    ) and not any(t in reply for t in forbidden)
                    report["checks"].append(
                        {
                            "id": label,
                            "reply": reply,
                            "expected": expected,
                            "forbidden": list(forbidden),
                            "passed": passed,
                        }
                    )

                if action == "seed":
                    for case in protocol["cases"]:
                        reply = send("/记住 " + case["key"] + "=" + case["value"])
                        if "已保存" not in reply:
                            raise RuntimeError("Seed fact was not accepted")
                    exported = json.loads(send("/导出记忆"))
                    observed = {r["predicate"]: r["value"] for r in exported["facts"]}
                    expected = {r["key"]: r["value"] for r in protocol["cases"]}
                    if observed != expected:
                        raise RuntimeError(
                            "Seed export does not match the frozen 20 facts"
                        )
                    from local_backup import snapshot

                    backup = snapshot()
                    seeded = datetime.now(timezone.utc)
                    protocol.update(
                        state="awaiting_real_day",
                        seeded_at=seeded.isoformat(),
                        not_before=(seeded + timedelta(hours=24)).isoformat(),
                        seed_evidence=output.name,
                        seed_facts=exported["facts"],
                        snapshot=backup["file"],
                    )
                    report.update(
                        facts_seeded=len(observed), backup=backup, completed=True
                    )
                    keep_facts = True
                else:
                    for index, case in enumerate(protocol["cases"], 1):
                        check(f"crossday-M01-{index:02d}", case["query"], case["value"])
                    check("crossday-M03-unknown", "我的生日是哪天")
                    check(
                        "crossday-M06-other",
                        "我的城市是什么",
                        forbidden=("青岛",),
                        user=protocol["other"],
                    )
                    for value, old in (("摇滚", "爵士"), ("爵士", "摇滚")):
                        send("/更正 音乐=" + value)
                        check(
                            "crossday-M02-" + value, "我现在喜欢什么音乐", value, (old,)
                        )
                    for case in protocol["cases"][:5]:
                        conversation = uuid.uuid4().hex
                        send("/忘记 " + case["key"], conversation=conversation)
                        check(
                            "crossday-M04-" + case["key"],
                            case["query"],
                            forbidden=(case["value"],),
                            conversation=conversation,
                        )
                    from local_backup import BACKUP_ROOT, verify_quarantine

                    report["restore"] = verify_quarantine(
                        BACKUP_ROOT / protocol["snapshot"]
                    )
                    report["completed"] = True
                    protocol["state"] = (
                        "verified"
                        if all(c["passed"] for c in report["checks"])
                        else "failed"
                    )
                    protocol["verify_evidence"] = output.name
            except Exception as exc:
                report["error_type"] = type(exc).__name__
                protocol["state"] = "failed"
                raise
            finally:
                if key is not None:
                    if not keep_facts:
                        try:
                            send("/忘记 全部")
                            send("/忘记 全部", user=protocol["other"])
                            protocol["cleanup_complete"] = report[
                                "synthetic_active_memory_cleared"
                            ] = True
                        except Exception:
                            protocol["cleanup_complete"] = report[
                                "synthetic_active_memory_cleared"
                            ] = False
                    client.headers["Authorization"] = bearer
                    try:
                        deleted = client.post(
                            "/api/apikey/delete", json={"key_id": key["key_id"]}
                        ).json()
                        report["temporary_key_deleted"] = deleted.get("status") == "ok"
                    except Exception:
                        report["temporary_key_deleted"] = False
                    if not keep_facts and not report.get("synthetic_active_memory_cleared"):
                        protocol["state"] = "cleanup_failed"
                    if not report["temporary_key_deleted"]:
                        protocol["pending_key_cleanup"] = key["key_id"]
                report["facts_intentionally_retained_for_real_day"] = keep_facts
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                save(protocol)
        return {
            "state": protocol["state"],
            "evidence": output.name,
            "not_before": protocol.get("not_before"),
            "facts_intentionally_retained": keep_facts,
            "temporary_key_deleted": report.get("temporary_key_deleted"),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "verify", "status"])
    print(json.dumps(run(parser.parse_args().action), ensure_ascii=False))
