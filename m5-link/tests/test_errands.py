import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
)
from m5_link.config import HeadConfig
from m5_link.errands import ErrandBook, should_hold
from m5_link.head import Head, HeadReply

ERRAND_REPORT = {"kind": "task-notification", "producer": "session-task"}


# ------------------------------------------------------------ the errand book


def test_an_errand_is_out_until_it_ends(tmp_path):
    book = ErrandBook(tmp_path / "held.json")
    book.started("t1", "今日のニュース調査", at=100.0)
    book.progressed("t1", "Searching for ニュース")
    assert book.running_list() == [
        {"task_id": "t1", "description": "今日のニュース調査", "started_at": 100.0, "progress": "Searching for ニュース"}
    ]
    assert book.finished("t1").description == "今日のニュース調査"
    assert book.running_list() == []
    assert book.finished("t1") is None  # a second end (update and notification both come) is harmless


def test_held_reports_wait_for_the_next_conversation_and_survive_a_restart(tmp_path):
    ErrandBook(tmp_path / "held.json").hold("COP31 が開幕したよ", at=1794189600.0)
    book = ErrandBook(tmp_path / "held.json")  # m5-link restarted
    notes, count = book.held_notes()
    assert notes.endswith("「COP31 が開幕したよ」") and count == 1


def test_only_the_reports_the_head_has_read_are_cleared(tmp_path):
    book = ErrandBook(tmp_path / "held.json")
    book.hold("一つめ")
    _, count = book.held_notes()
    book.hold("二つめ")  # came back while the head was reading the first
    book.clear_held(count)
    notes, count = book.held_notes()
    assert "二つめ" in notes and "一つめ" not in notes and count == 1


def test_a_sleeping_or_absent_body_keeps_the_report():
    assert should_hold(body_connected=True, body_asleep=True)
    assert should_hold(body_connected=False, body_asleep=False)
    assert not should_hold(body_connected=True, body_asleep=False)


# ------------------------------------------------------------ sorting the session's messages


def make_head(tmp_path) -> Head:
    head = Head(HeadConfig())
    head.errands = ErrandBook(tmp_path / "held.json")
    return head


def init() -> SystemMessage:
    return SystemMessage(subtype="init", data={})


def said(text: str, parent: str | None = None) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="sonnet", parent_tool_use_id=parent)


def turn_end(origin: dict | None = None) -> ResultMessage:
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                         session_id="s1", origin=origin)


def test_the_reply_to_our_prompt_goes_to_whoever_waits_for_it(tmp_path):
    async def scenario():
        head = make_head(tmp_path)
        head._waiting_reply = asyncio.get_running_loop().create_future()
        head.take_in(init())
        head.take_in(said("[happy] 見てくるね"))
        head.take_in(said("今日のニュースはこれ", parent="toolu_agent"))  # the errand's own words, not the head's
        head.take_in(turn_end())
        return await head._waiting_reply

    reply = asyncio.run(scenario())
    assert (reply.text, reply.mood) == ("見てくるね", "happy")


def test_a_turn_the_session_starts_for_a_returned_errand_is_a_report(tmp_path):
    reports: list[HeadReply] = []

    async def scenario():
        head = make_head(tmp_path)

        async def collect(reply: HeadReply) -> None:
            reports.append(reply)

        head.on_errand_report = collect
        head.take_in(TaskStartedMessage(subtype="task_started", data={}, task_id="t1",
                                        description="今日のニュース調査", uuid="u", session_id="s1"))
        head.take_in(TaskProgressMessage(subtype="task_progress", data={}, task_id="t1",
                                         description="Searching for ニュース", usage={}, uuid="u", session_id="s1"))
        assert head.summary()["errands"][0]["progress"] == "Searching for ニュース"
        head.take_in(TaskUpdatedMessage(subtype="task_updated", data={}, task_id="t1", patch={"status": "completed"}))
        head.take_in(TaskNotificationMessage(subtype="task_notification", data={}, task_id="t1", status="completed",
                                             output_file="", summary="COP31 が開幕", uuid="u", session_id="s1"))
        assert head.summary()["errands"] == []
        head.take_in(init())
        assert not head._idle.is_set()  # our next prompt waits for this turn
        head.take_in(said("COP31 が今日開幕したんだって"))
        head.take_in(turn_end(ERRAND_REPORT))
        assert head._idle.is_set()
        await asyncio.sleep(0)  # let the report callback run

    asyncio.run(scenario())
    assert [r.text for r in reports] == ["COP31 が今日開幕したんだって"]


def test_a_stopped_errand_is_no_longer_out(tmp_path):
    head = make_head(tmp_path)
    head.take_in(TaskStartedMessage(subtype="task_started", data={}, task_id="t1",
                                    description="今日のニュース調査", uuid="u", session_id="s1"))
    head.take_in(TaskUpdatedMessage(subtype="task_updated", data={}, task_id="t1", patch={"status": "killed"}))
    assert head.summary()["errands"] == []
