"""Persist attempt counts under a process lock; honor explicit unlimited testing."""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock


def reserve_attempt(path: Path, content_bytes: int) -> int:
    """Record one bounded request without inventing a limit for unlimited testing.

    Legacy finite grants remain readable for rollback; only an explicit host-side
    unlimited authorization bypasses their counters, date and monetary reserve.
    Every writer uses the same lock, avoiding partial reads on encrypted volumes.
    """
    if content_bytes > 13000:
        raise ValueError("上下文超过单次输入上限。")
    with FileLock(str(path) + ".lock", timeout=5):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("mode") != "unlimited_testing":
            today = datetime.now(ZoneInfo("America/Tijuana")).date().isoformat()
            if record.get("authorized_date") != today:
                raise ValueError("测试授权日期已过期；没有自动续费或重置额度。")
            reservation = record.get("reservation_per_call_cny", 0.2)
            if not 0.05 <= reservation <= 0.2:
                raise ValueError("调用费用预留配置无效。")
            if (
                record["remaining_calls"] <= 0
                or record.get("reserved_cny_remaining", 0) < reservation
            ):
                raise ValueError("本轮真实模型测试额度已用完。")
            record["remaining_calls"] -= 1
            record["reserved_cny_remaining"] = round(
                record["reserved_cny_remaining"] - reservation, 4
            )
        record["attempts_used"] = record.get("attempts_used", 0) + 1
        with path.open("w", encoding="utf-8") as output:
            json.dump(record, output)
            output.flush()
            os.fsync(output.fileno())
        return record["attempts_used"]
