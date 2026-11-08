from datetime import datetime

from m5_link.head import conversation_day, next_day_start, tool_fingerprint


def test_small_hours_still_belong_to_the_day_before():
    assert conversation_day(datetime(2026, 11, 9, 3, 59), day_starts_at=4) == "2026-11-08"
    assert conversation_day(datetime(2026, 11, 9, 4, 0), day_starts_at=4) == "2026-11-09"


def test_the_next_day_starts_at_the_set_hour():
    assert next_day_start(datetime(2026, 11, 8, 21, 0), 4) == datetime(2026, 11, 9, 4, 0)
    assert next_day_start(datetime(2026, 11, 9, 3, 0), 4) == datetime(2026, 11, 9, 4, 0)


def test_the_fingerprint_follows_the_set_of_mcp_servers_not_their_order():
    assert tool_fingerprint(["memory", "body"]) == tool_fingerprint(["body", "memory"])
    assert tool_fingerprint(["memory", "body"]) != tool_fingerprint(["memory"])
