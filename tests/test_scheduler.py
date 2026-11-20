from datetime import datetime, timezone

from tt2x.scheduler import due_slot, upcoming_slots

UTC = timezone.utc
SLOTS = ["09:00", "14:00", "20:00"]


def at(h, m=0, day=10):
    return datetime(2026, 11, day, h, m, tzinfo=UTC)


def test_upcoming_rolls_to_next_day():
    got = upcoming_slots(at(15), SLOTS, "UTC", 4)
    assert got == [at(20), at(9, day=11), at(14, day=11), at(20, day=11)]


def test_upcoming_respects_timezone():
    # 09:00 in Addis Ababa (UTC+3) is 06:00 UTC
    got = upcoming_slots(at(5), ["09:00"], "Africa/Addis_Ababa", 1)[0]
    assert got.astimezone(UTC) == at(6)


def test_due_once_per_slot():
    slot = due_slot(at(14, 5), SLOTS, "UTC", None, 120)
    assert slot == at(14)
    assert due_slot(at(14, 6), SLOTS, "UTC", slot, 120) is None  # already served
    assert due_slot(at(20, 1), SLOTS, "UTC", slot, 120) == at(20)


def test_missed_slot_outside_grace_is_skipped():
    assert due_slot(at(17), SLOTS, "UTC", None, 120) is None  # 14:00 was 3h ago


def test_slot_before_first_of_day_uses_yesterday():
    assert due_slot(at(1), SLOTS, "UTC", None, 600) == at(20, day=9)
