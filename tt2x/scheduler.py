"""Posting slots: which slot is due now, and when upcoming queued items will go out."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def _parse_slots(slots: list[str]) -> list[time]:
    out = []
    for s in slots:
        h, m = s.strip().split(":")
        out.append(time(int(h), int(m)))
    return sorted(out)


def upcoming_slots(now: datetime, slots: list[str], tz: str, n: int) -> list[datetime]:
    """The next `n` slot datetimes strictly after `now` (aware, in `tz`)."""
    zone = ZoneInfo(tz)
    now = now.astimezone(zone)
    parsed = _parse_slots(slots)
    if not parsed or n <= 0:
        return []
    out: list[datetime] = []
    day = now.date()
    while len(out) < n:
        for t in parsed:
            dt = datetime.combine(day, t, tzinfo=zone)
            if dt > now:
                out.append(dt)
                if len(out) == n:
                    break
        day += timedelta(days=1)
    return out


def latest_slot_at_or_before(now: datetime, slots: list[str], tz: str) -> datetime | None:
    zone = ZoneInfo(tz)
    now = now.astimezone(zone)
    parsed = _parse_slots(slots)
    for back in range(0, 2):
        day = now.date() - timedelta(days=back)
        for t in reversed(parsed):
            dt = datetime.combine(day, t, tzinfo=zone)
            if dt <= now:
                return dt
    return None


def due_slot(
    now: datetime, slots: list[str], tz: str, last_done: datetime | None, grace_minutes: int
) -> datetime | None:
    """The slot that should be served now, or None.

    A slot is due once, if it has not been served yet and we are within
    `grace_minutes` of it (so a computer that was asleep for hours does not
    fire a burst of old slots when it wakes up).
    """
    slot = latest_slot_at_or_before(now, slots, tz)
    if slot is None:
        return None
    if last_done is not None and slot <= last_done:
        return None
    if now.astimezone(slot.tzinfo) - slot > timedelta(minutes=grace_minutes):
        return None
    return slot
