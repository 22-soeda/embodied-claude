from datetime import datetime

from m5_link.body_diary import (
    MAX_DIGEST_LINES,
    BodyDiary,
    DiaryEntry,
    format_digest,
    group_into_stretches,
)

EVENING = datetime(2026, 11, 8, 20, 0)


def at(hour: int, minute: int, day: int = 8) -> float:
    return datetime(2026, 11, day, hour, minute).timestamp()


def test_repeats_close_together_become_one_stretch_even_with_other_kinds_between():
    entries = [
        DiaryEntry("picked_up", at(20, 11)),
        DiaryEntry("near", at(20, 12)),
        DiaryEntry("picked_up", at(20, 14)),
        DiaryEntry("picked_up", at(20, 16)),
    ]
    stretches = group_into_stretches(entries)
    assert [(s.kind, s.count) for s in stretches] == [("picked_up", 3), ("near", 1)]


def test_a_long_pause_starts_a_new_stretch():
    entries = [DiaryEntry("shaken", at(20, 0)), DiaryEntry("shaken", at(20, 30))]
    assert [s.count for s in group_into_stretches(entries)] == [1, 1]


def test_digest_lines_read_like_a_diary():
    entries = [
        DiaryEntry("battery_low", at(23, 10, day=7)),
        DiaryEntry("picked_up", at(20, 11)),
        DiaryEntry("picked_up", at(20, 16)),
    ]
    assert format_digest(entries, now=EVENING).splitlines() == [
        "11/07 23:10 電池が少なくなった",
        "20:11〜20:16 持ち上げられた（2 回）",
    ]


def test_a_long_digest_keeps_the_newest_lines():
    entries = [DiaryEntry("shaken", at(8 + i, 0)) for i in range(MAX_DIGEST_LINES + 3)]
    lines = format_digest(entries, now=EVENING).splitlines()
    assert lines[0] == "（それより前にも 3 件）"
    assert len(lines) == MAX_DIGEST_LINES + 1
    assert lines[-1].startswith("22:00")


def test_what_the_body_said_keeps_its_words_and_is_never_merged():
    entries = [
        DiaryEntry("said", at(20, 0), text="そういえば、今日はどうだった？"),
        DiaryEntry("said", at(20, 1), text="おかえり"),
    ]
    assert format_digest(entries, now=EVENING).splitlines() == [
        "20:00 自分から体の声で話しかけた「そういえば、今日はどうだった？」",
        "20:01 自分から体の声で話しかけた「おかえり」",
    ]


def test_unread_notes_are_handed_over_once(tmp_path):
    diary = BodyDiary(tmp_path)
    diary.record("picked_up")
    diary.record("connected")  # m5-link's own business: not written down
    notes, until = diary.unread("conversation")
    assert "持ち上げられた" in notes and "connected" not in notes

    diary.mark_read("conversation", until)
    assert diary.unread("conversation")[0] == ""


def test_each_reader_keeps_its_own_mark(tmp_path):
    diary = BodyDiary(tmp_path)
    diary.record("said", text="おかえり")
    _, until = diary.unread("heartbeat")
    diary.mark_read("heartbeat", until)  # the next heartbeat read it first
    assert "おかえり" in diary.unread("conversation")[0]


def test_the_read_mark_never_moves_back(tmp_path):
    diary = BodyDiary(tmp_path)
    diary.mark_read("conversation", 200.0)
    diary.mark_read("conversation", 100.0)
    assert diary.read_until("conversation") == 200.0
