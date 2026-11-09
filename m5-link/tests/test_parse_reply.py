from m5_link.head import parse_reply


def test_a_line_break_tag_is_not_read_aloud():
    # On 2026-11-09 the head ended a reply with "</br>" and the voice read it out.
    reply = parse_reply("[happy] おはよう、今朝7時に起きたよ。</br>")
    assert reply.text == "おはよう、今朝7時に起きたよ。"
    assert reply.mood == "happy"


def test_line_break_tags_between_sentences_become_a_space():
    assert parse_reply("おはよう<br>今朝起きたよ<BR />").text == "おはよう 今朝起きたよ"
