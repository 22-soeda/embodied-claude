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


def test_a_broken_message_stream_resumes_the_same_session(monkeypatch):
    """2026-11-11: a message over the SDK's buffer broke the stream while an errand ran; the head stayed deaf."""
    import asyncio

    from m5_link.config import HeadConfig
    from m5_link.head import Head, SessionState

    class BrokenClient:
        disconnected = False

        async def receive_messages(self):
            raise RuntimeError("JSON message exceeded maximum buffer size")
            yield  # an async generator that breaks at once

        async def disconnect(self):
            self.disconnected = True

    head = Head(HeadConfig())
    broken = BrokenClient()
    head._client, head._state = broken, SessionState(session_id="s1", day="2026-11-11")
    resumed = []

    async def fake_connect(state):
        resumed.append(state.session_id)
        head._client = object()

    monkeypatch.setattr(head, "_connect", fake_connect)

    async def run():
        await head._read(broken)
        await asyncio.gather(*head._background)

    asyncio.run(run())
    assert broken.disconnected and resumed == ["s1"]
