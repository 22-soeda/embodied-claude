"""One turn of conversation: something comes in, the head answers, the body says it.

    voice from the body -> ears -> head -> mouth -> body speaks
    text from the web page ----------^
    "みてみて" picture ----(saved)---^
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .body import Body, BodyEvent
from .config import REPO_ROOT
from .ears import Ears
from .head import Head, HeadReply
from .mouth import Mouth

log = logging.getLogger(__name__)

CAPTURE_DIR = REPO_ROOT / "captures"  # ignored by Git in the upstream .gitignore
SPOKEN_EVENTS = {"picked_up", "shaken"}  # body events worth a word from the head


@dataclass
class LogLine:
    who: str  # "human" | "head" | "system"
    text: str
    source: str = ""
    mood: str | None = None
    at: float = field(default_factory=time.time)


class Conversation:
    def __init__(self, body: Body, ears: Ears, head: Head, mouth: Mouth) -> None:
        self.body, self.ears, self.head, self.mouth = body, ears, head, mouth
        self.lines: deque[LogLine] = deque(maxlen=200)
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
        if event.kind not in SPOKEN_EVENTS or self.head.is_thinking or self.body.is_speaking:
            return
        descriptions = {"picked_up": "体が持ち上げられた", "shaken": "体が揺さぶられた"}
        await self.respond(descriptions[event.kind], source="体", log_input=False)

    # ------------------------------------------------------------ the turn

    async def respond(self, text: str, *, source: str, log_input: bool = True) -> HeadReply:
        if log_input:
            self._add(LogLine("human", text, source=source))
        if not self.head.is_ready:
            await self._say_trouble("まだ起きている途中。もう少し待って")
            return HeadReply(is_silent=True)

        await self.body.set_thinking(True)
        started = time.monotonic()
        try:
            reply = await self.head.reply(f"[{source}] {text}")
        except Exception as error:  # noqa: BLE001
            log.exception("the head failed to answer")
            await self._say_trouble(f"頭がうまく働かなかった（{error}）")
            return HeadReply(is_silent=True)
        think_seconds = time.monotonic() - started

        if reply.is_silent:
            self._add(LogLine("head", "（黙っている）"))
            await self.body.set_thinking(False)
            return reply

        self._add(LogLine("head", reply.text, mood=reply.mood))
        log.info("head answered in %.1f s (cost %s): %s", think_seconds, reply.cost_usd, reply.text)
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

    async def _say_trouble(self, text: str) -> None:
        self._add(LogLine("system", text))
        await self.body.show_face("sad", 4000)
        await self.body.send({"t": "caption", "text": text, "ms": 6000})
        await self.body.set_thinking(False)

    def _add(self, line: LogLine) -> None:
        self.lines.append(line)

    def recent_lines(self, limit: int = 50) -> list[dict]:
        return [asdict(line) for line in list(self.lines)[-limit:]]
