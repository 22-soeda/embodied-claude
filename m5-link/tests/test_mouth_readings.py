from m5_link.mouth import parse_reading


def test_a_bare_reading_has_a_flat_accent():
    assert parse_reading("ショウジ") == ("ショウジ", 0)


def test_a_reading_can_say_where_the_pitch_falls():
    assert parse_reading(["ハシ", 1]) == ("ハシ", 1)
