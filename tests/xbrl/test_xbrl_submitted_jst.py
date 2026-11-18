from datetime import datetime, timedelta, timezone

import pytest

from app.xbrl.store import _parse_submitted

JST = timezone(timedelta(hours=9))


@pytest.mark.parametrize("raw", ["", "   ", "not a date"])
def test_unreadable_submitted_falls_back_to_jst_now(raw):
    before = datetime.now(JST).replace(tzinfo=None)
    got = _parse_submitted(raw)
    after = datetime.now(JST).replace(tzinfo=None)
    assert before <= got <= after
    assert got.tzinfo is None


def test_readable_submitted_is_kept():
    assert _parse_submitted("2026-05-12 15:30:00") == datetime(2026, 5, 12, 15, 30)
    assert _parse_submitted("2026-05-12T15:30:00") == datetime(2026, 5, 12, 15, 30)
    assert _parse_submitted("2026-05-12") == datetime(2026, 5, 12)
