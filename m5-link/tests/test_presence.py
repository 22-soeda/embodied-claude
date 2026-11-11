"""One agent, two bodies: the M5 at home and the phone. It is in the one last called."""

import asyncio
import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from m5_link.body import Body
from m5_link.body_diary import BodyDiary
from m5_link.config import LinkConfig, ServerConfig
from m5_link.conversation import Conversation
from m5_link.head import HeadReply
from m5_link.presence import Presence
from m5_link.server import build_app


class FakeBody:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.spoken: list[str] = []
        self.is_connected = True
        self.state: dict = {}
        self.on_call = self.on_connect = self.on_utterance = self.on_text = None
        self.on_mitemite = self.on_event = None

    async def send(self, message: dict) -> bool:
        self.sent.append(message)
        return True

    async def show_face(self, mood: str, duration_ms: int = 0) -> bool:
        return await self.send({"t": "face", "mood": mood})

    async def set_thinking(self, on: bool) -> bool:
        return await self.send({"t": "thinking", "on": on})

    async def speak(self, pcm: bytes, text: str, *, wait: bool = True) -> bool:
        self.spoken.append(text)
        return True


class FakeHead:
    is_ready = True

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.on_errand_report = None
        self.errands = self

    async def reply(self, prompt: str) -> HeadReply:
        self.prompts.append(prompt)
        return HeadReply(text="うん、聞こえてる")

    def held_notes(self) -> tuple[str, int]:
        return "", 0


class FakeEars:
    async def transcribe(self, pcm: bytes) -> str:
        return "聞こえる？"


class FakeMouth:
    is_ready = True

    async def synthesize(self, text: str) -> bytes:
        return b"\0\0"


class FakeDesires:
    async def talked(self, how: str) -> None:
        pass


def make_presence(tmp_path: Path) -> Presence:
    return Presence({"m5": FakeBody(), "phone": FakeBody()}, tmp_path / "presence.json", BodyDiary(tmp_path / "diary"))


def test_the_agent_starts_at_home(tmp_path):
    assert make_presence(tmp_path).where == "m5"


def test_calling_the_phone_empties_the_m5_and_is_written_down(tmp_path):
    presence = make_presence(tmp_path)
    asyncio.run(presence.call("phone"))

    assert presence.where == "phone"
    assert presence.bodies["m5"].sent == [{"t": "away", "on": True, "text": "スマホにいるよ"}]
    assert presence.bodies["phone"].sent == [{"t": "away", "on": False, "text": ""}]
    assert "スマホに移った" in BodyDiary(tmp_path / "diary").recent(1)
    assert make_presence(tmp_path).where == "phone"  # a restart does not bring it home


def test_calling_the_body_it_is_in_changes_nothing(tmp_path):
    presence = make_presence(tmp_path)
    asyncio.run(presence.call("m5"))
    assert presence.bodies["m5"].sent == [] and presence.bodies["phone"].sent == []


def test_the_reply_goes_back_to_the_body_that_was_spoken_to(tmp_path):
    presence = make_presence(tmp_path)
    head = FakeHead()
    conversation = Conversation(presence, FakeEars(), head, FakeMouth(), BodyDiary(tmp_path / "diary"), FakeDesires())

    async def talk_through_the_phone():
        await presence.bodies["phone"].on_call()  # what the phone's listen_start does
        await conversation.hear_voice(b"\0\0" * 16000, False, "phone")

    asyncio.run(talk_through_the_phone())

    assert head.prompts[-1].endswith("[スマホの声] 聞こえる？")
    assert presence.bodies["phone"].spoken == ["うん、聞こえてる"]
    assert presence.bodies["m5"].spoken == []
    assert {"t": "heard", "text": "聞こえる？"} in presence.bodies["phone"].sent


def test_typed_text_on_the_phone_calls_the_agent_there():
    body = Body("phone")
    events = []

    async def called():
        events.append("called")

    async def typed(text):
        events.append(text)

    body.on_call, body.on_text = called, typed

    async def run():
        await body._handle_text({"t": "text", "text": " ただいま "})
        await body._handle_text({"t": "text", "text": "   "})  # nothing typed: no call
        await asyncio.sleep(0)

    asyncio.run(run())
    assert events == ["called", "ただいま"]


def test_through_tailscale_serve_the_phone_page_needs_the_token(tmp_path):
    presence = make_presence(tmp_path)
    conversation = Conversation(presence, FakeEars(), FakeHead(), FakeMouth(), BodyDiary(tmp_path / "diary"), FakeDesires())
    config = LinkConfig(server=ServerConfig(token="secret"))
    forwarded = {"X-Forwarded-For": "100.127.244.109"}  # what tailscale serve adds

    async def statuses() -> list[int]:
        async with TestClient(TestServer(build_app(config, conversation, None, None, "test"))) as client:
            results = []
            for path, headers in [("/phone/", {}), ("/phone/", forwarded), ("/phone/?token=secret", forwarded),
                                  ("/phone/manifest.json", forwarded), ("/api/status", forwarded)]:
                async with client.get(path, headers=headers) as response:
                    results.append(response.status)
            return results

    assert asyncio.run(statuses()) == [200, 401, 200, 200, 401]


def test_the_manifest_names_the_app_and_leaves_the_start_to_the_page(tmp_path):
    presence = make_presence(tmp_path)
    conversation = Conversation(presence, FakeEars(), FakeHead(), FakeMouth(), BodyDiary(tmp_path / "diary"), FakeDesires())

    async def manifest() -> dict:
        async with TestClient(TestServer(build_app(LinkConfig(), conversation, None, None, "test"))) as client:
            async with client.get("/phone/manifest.json") as response:
                return json.loads(await response.text())

    result = asyncio.run(manifest())
    assert result["display"] == "standalone" and "start_url" not in result
