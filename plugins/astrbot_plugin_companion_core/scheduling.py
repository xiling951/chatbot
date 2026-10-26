"""UTC instants, explicit IANA zones, and deterministic missed-slot rules."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(timezone.utc)


def daily_slot(day: date, hour: int, minute: int, zone: str) -> datetime:
    """First fold for repeated times; first valid minute after a missing time."""
    tz = ZoneInfo(zone)
    wall = datetime.combine(day, time(hour, minute))
    for _ in range(181):
        candidate = wall.replace(tzinfo=tz, fold=0)
        result = utc(candidate)
        if result.astimezone(tz).replace(tzinfo=None) == wall:
            return result
        wall += timedelta(minutes=1)
    raise ValueError("No valid daily slot within the supported three-hour transition")


def due_slot(
    next_run: datetime, current: datetime, rule: dict, zone: str
) -> tuple[datetime | None, datetime | None]:
    """Coalesce downtime into the latest due slot, never a burst of missed slots."""
    next_run, current = utc(next_run), utc(current)
    tz = ZoneInfo(zone)
    if next_run > current:
        return None, next_run
    kind = rule.get("kind")
    if kind == "once":
        return next_run, None
    if kind == "interval":
        seconds = rule.get("seconds")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int)
            or not 60 <= seconds <= 30 * 86400
        ):
            raise ValueError(
                "Interval must be an integer between 60 seconds and 30 days"
            )
        step = timedelta(seconds=seconds)
        latest = next_run + ((current - next_run) // step) * step
        return latest, latest + step
    if kind == "daily":
        hour, minute = rule["hour"], rule["minute"]
        today = current.astimezone(tz).date()
        latest = daily_slot(today, hour, minute, zone)
        if latest > current:
            latest = daily_slot(today - timedelta(days=1), hour, minute, zone)
        if latest < next_run:
            return None, next_run
        following = daily_slot(
            latest.astimezone(tz).date() + timedelta(days=1), hour, minute, zone
        )
        return latest, following
    raise ValueError("Unsupported schedule kind")


def in_quiet_hours(current: datetime, zone: str, start=23, end=8) -> bool:
    if (
        not all(
            isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23
            for h in (start, end)
        )
        or start == end
    ):
        raise ValueError("Quiet hours require two different hours in 0..23")
    hour = utc(current).astimezone(ZoneInfo(zone)).hour
    return start <= hour or hour < end if start > end else start <= hour < end
