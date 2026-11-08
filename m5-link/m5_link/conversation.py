"""One turn of conversation: something comes in, the head answers, the body says it.

    voice from the body -> ears -> head -> mouth -> body speaks
    text from the web page ----------^
    "みてみて" picture ----(saved)---^

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
from .body_diary import BodyDiary
from .config import REPO_ROOT
from .desires import Desires
from .ears import Ears
from .head import Head, HeadReply
from .mouth import Mouth

log = logging.getLogger(__name__)

CAPTURE_DIR = REPO_ROOT / "captures"  # ignored by Git in the upstream .gitignore
DIARY_READER = "conversation"  # this session's read mark in the body diary


@dataclass
class LogLine:
    who: str  # "human" | "head" | "system"
    text: str
    source: str = ""
    mood: str | None = None
    at: float = field(default_factory=time.time)


class Conversation:
    def __init__(self, body: Body, ears: Ears, head: Head, mouth: Mouth, diary: BodyDiary,
                 desires: Desires) -> None:
        self.body, self.ears, self.head, self.mouth = body, ears, head, mouth
        self.diary = diary
        self.desires = desires
        self.lines: deque[LogLine] = deque(maxlen=200)
        self._background: set[asyncio.Task] = set()
        body.on_utterance = self.hear_voice
        body.on_mitemite = self.see_picture
        body.on_event = self.feel_event

    # ------------------------------------------------------------ inputs

    async def hear_voice(self, pcm: bytes, is_loop: bool) -> None:
        seconds = len(pcm) / 2 / 16000
        try:
            text = await self.ears.transcribe(pcm)
        except Exception as error:  # noqa: BLE001
            log.exception("speech recognition failed")
            await self._say_trouble(f"耳の調子が悪いみたい（{error}）")
            return
        log.info("heard %.1f s: %s", seconds, text)
        if not text:
            await self.body.set_thinking(False)  # in a loop, the body listens again
            return
        await self.respond(text, source="声")

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
        self.diary.record(event.kind, event.at)

    # ------------------------------------------------------------ the turn

    async def respond(self, text: str, *, source: str, log_input: bool = True) -> HeadReply:
        if log_input:
            self._add(LogLine("human", text, source=source))
        if not self.head.is_ready:
            await self._say_trouble("まだ起きている途中。もう少し待って")
            return HeadReply(is_silent=True)

        await self.body.set_thinking(True)
        body_notes, notes_until = self.diary.unread(DIARY_READER)
        prompt = f"[{source}] {text}"
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
        pcm = await self.mouth.synthesize(text)
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
