"""One turn of conversation: something comes in, the head answers, the body says it.

    voice from a body -> ears -> head -> mouth -> the body the agent is in speaks
    text from the phone or the web page ^
    "みてみて" picture ----(saved)------^

The agent is in one body at a time (presence.py): the one the companion last
turned to, so the reply goes back where the companion spoke.

Other body events (picked up, shaken, someone near) do not start a turn:
they go into the body diary, and the next turn carries the unread part.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .body import Body, BodyEvent
from .body_diary import BodyDiary, diary_kind
from .config import REPO_ROOT
from .desires import Desires
from .ears import Ears
from .errands import should_hold
from .head import Head, HeadReply
from .mouth import Mouth
from .presence import Presence

log = logging.getLogger(__name__)

CAPTURE_DIR = REPO_ROOT / "captures"  # ignored by Git in the upstream .gitignore
DIARY_READER = "conversation"  # this session's read mark in the body diary
# The input marks the head reads (head.py's system prompt), by the body the words came through.
VOICE_MARK = {"m5": "声", "phone": "スマホの声"}
TEXT_MARK = "スマホの文字"


@dataclass
class LogLine:
    who: str  # "human" | "head" | "system"
    text: str
    source: str = ""
    mood: str | None = None
    at: float = field(default_factory=time.time)


class Conversation:
    def __init__(self, presence: Presence, ears: Ears, head: Head, mouth: Mouth, diary: BodyDiary,
                 desires: Desires) -> None:
        self.presence, self.ears, self.head, self.mouth = presence, ears, head, mouth
        self.diary = diary
        self.desires = desires
        self.lines: deque[LogLine] = deque(maxlen=200)
        self._background: set[asyncio.Task] = set()
        for name, body in presence.bodies.items():
            body.on_call = lambda name=name: presence.call(name)
            body.on_connect = lambda name=name: presence.tell(name)
            body.on_utterance = lambda pcm, is_loop, name=name: self.hear_voice(pcm, is_loop, name)
            body.on_text = self.read_text
            body.on_mitemite = self.see_picture
            body.on_event = self.feel_event
        head.on_errand_report = self.tell_errand_report

    @property
    def body(self) -> Body:
        """The body the agent is in now."""
        return self.presence.body

    # ------------------------------------------------------------ inputs

    async def hear_voice(self, pcm: bytes, is_loop: bool, place: str = "m5") -> None:
        seconds = len(pcm) / 2 / 16000
        try:
            text = await self.ears.transcribe(pcm)
        except Exception as error:  # noqa: BLE001
            log.exception("speech recognition failed")
            await self._say_trouble(f"耳の調子が悪いみたい（{error}）")
            return
        log.info("heard %.1f s through %s: %s", seconds, place, text)
        # What was heard, for a screen that can show it (the phone; the M5 ignores it).
        await self.presence.bodies[place].send({"t": "heard", "text": text})
        if not text:
            await self.body.set_thinking(False)  # in a loop, the body listens again
            return
        await self.respond(text, source=VOICE_MARK[place])

    async def read_text(self, text: str) -> None:
        await self.respond(text, source=TEXT_MARK)

    async def see_picture(self, jpeg: bytes) -> None:
        CAPTURE_DIR.mkdir(exist_ok=True)
        path = CAPTURE_DIR / f"mitemite_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        path.write_bytes(jpeg)
        relative = path.relative_to(REPO_ROOT).as_posix()
        self._add(LogLine("human", f"（写真を見せた: {relative}）", source="みてみて"))
        await self.respond(f"カメラで見せてくれた。画像: {relative}", source="みてみて", log_input=False)

    async def feel_event(self, event: BodyEvent) -> None:
        # The body has already reacted by itself (a surprised face). The head
        # hears about it at its next turn, so carrying the body around does
        # not turn into a remark per lift.
        self.diary.record(diary_kind(event.kind, event.detail.get("reason")), event.at)
        if event.kind == "slept":
            self._in_background(self.desires.slept(event.detail.get("reason") or "head"))

    # ------------------------------------------------------------ the turn

    async def respond(self, text: str, *, source: str, log_input: bool = True) -> HeadReply:
        if log_input:
            self._add(LogLine("human", text, source=source))
        if not self.head.is_ready:
            await self._say_trouble("まだ起きている途中。もう少し待って")
            return HeadReply(is_silent=True)

        await self.body.set_thinking(True)
        body_notes, notes_until = self.diary.unread(DIARY_READER)
        held_reports, held_count = self.head.errands.held_notes()
        prompt = f"[{source}] {text}"
        if held_reports:
            prompt = f"[言えなかった報告]\n{held_reports}\n\n{prompt}"
        if body_notes:
            prompt = f"[体の記録]\n{body_notes}\n\n{prompt}"
        started = time.monotonic()
        try:
            reply = await self.head.reply(prompt)
        except Exception as error:  # noqa: BLE001
            log.exception("the head failed to answer")
            await self._say_trouble(f"頭がうまく働かなかった（{error}）")
            return HeadReply(is_silent=True)
        think_seconds = time.monotonic() - started
        if body_notes:
            self.diary.mark_read(DIARY_READER, notes_until)  # only once the head has really read them
            self._add(LogLine("system", "体の記録を添えた：" + body_notes.replace("\n", " / ")))
        if held_reports:
            self.head.errands.clear_held(held_count)
            self._add(LogLine("system", "言えなかった報告を添えた：" + held_reports.replace("\n", " / ")))
        # The person spoke to the body: that is talking together, whatever the
        # head answers. Recorded in the background, so the reply is not held up.
        self._in_background(self.desires.talked(source))

        if reply.is_silent:
            self._add(LogLine("head", "（黙っている）"))
            await self.body.set_thinking(False)
            return reply

        self._add(LogLine("head", reply.text, mood=reply.mood))
        log.info("head answered in %.1f s (read %s tokens): %s", think_seconds, reply.tokens_read, reply.text)
        if reply.mood:
            await self.body.show_face(reply.mood, 6000)
        await self.speak(reply.text)
        return reply

    async def speak(self, text: str) -> bool:
        """Say something through the body (used by the turn and by the body MCP)."""
        if not self.body.is_connected:
            return False
        if not self.mouth.is_ready:
            await self.body.send({"t": "caption", "text": text, "ms": 8000})
            await self.body.set_thinking(False)
            return True
        started = time.monotonic()
        pcm = await self.mouth.synthesize(text)
        log.info("speech synthesized: %d characters in %.1f s", len(text), time.monotonic() - started)
        return await self.body.speak(pcm, text)

    async def speak_unprompted(self, text: str, mood: str | None = None) -> bool:
        """Say something nobody asked for (the heartbeat's body MCP `say`), and write it down.

        The conversation head runs in another session; the diary is how it
        learns, at the next turn, what the body has just said to the person.
        """
        if mood:
            await self.body.show_face(mood, 6000)
        spoken = await self.speak(text)
        if spoken:
            self._add(LogLine("head", text, source="自分から", mood=mood))
            self.diary.record("said", text=text)
            log.info("the body said on its own: %s", text)
        return spoken

    async def tell_errand_report(self, reply: HeadReply) -> None:
        """The head's words on an errand that came back: say them now, or keep them if the body sleeps."""
        if reply.is_silent:
            self._add(LogLine("head", "（おつかいの報告に、黙っている）", source="おつかい"))
            return
        log.info("head reported an errand (read %s tokens): %s", reply.tokens_read, reply.text)
        if should_hold(body_connected=self.body.is_connected, body_asleep=bool(self.body.state.get("asleep"))):
            self.head.errands.hold(reply.text)
            self._add(LogLine("system", f"体が寝ているので、おつかいの報告を次の会話まで取っておく：{reply.text}"))
            return
        if reply.mood:
            await self.body.show_face(reply.mood, 6000)
        if await self.speak(reply.text):
            self._add(LogLine("head", reply.text, source="おつかい", mood=reply.mood))
            self.diary.record("reported", text=reply.text)  # so the heartbeat knows it was said
        else:
            self.head.errands.hold(reply.text)

    async def _say_trouble(self, text: str) -> None:
        self._add(LogLine("system", text))
        await self.body.show_face("sad", 4000)
        await self.body.send({"t": "caption", "text": text, "ms": 6000})
        await self.body.set_thinking(False)

    def _in_background(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background.add(task)  # keep a reference until it finishes
        task.add_done_callback(self._background.discard)

    def _add(self, line: LogLine) -> None:
        self.lines.append(line)

    def recent_lines(self, limit: int = 50) -> list[dict]:
        return [asdict(line) for line in list(self.lines)[-limit:]]
