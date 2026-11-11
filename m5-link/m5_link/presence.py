"""Which body the agent is in: one at a time, the one the companion last called.

There are two bodies: the M5 on the desk at home ("m5") and the phone page
taken outside ("phone"). Turning to a body - beginning to talk to it, typing
on it, showing it a picture - calls the agent there. The other body is told
that the agent is away and shows where it went instead of a face, so the
agent never looks like two. Replies, things said unprompted and faces go only
to the body it is in.

Being away is not sleeping: the agent has moved, nothing more, so no desire
is touched. The place is kept in a file, so a restart does not bring the
agent home.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .body import Body
from .body_diary import BodyDiary

log = logging.getLogger(__name__)

HOME = "m5"
# What a body shows while the agent is in the other one.
WHERE_IT_WENT = {"m5": "家の M5 にいるよ", "phone": "スマホにいるよ"}


class Presence:
    def __init__(self, bodies: dict[str, Body], path: Path, diary: BodyDiary) -> None:
        self.bodies = bodies
        self._path = path
        self._diary = diary
        self.where = self._load()

    @property
    def body(self) -> Body:
        return self.bodies[self.where]

    async def call(self, name: str) -> None:
        """The companion turned to this body: the agent comes here."""
        if name == self.where:
            return
        self.where = name
        self._path.write_text(json.dumps({"where": name}), encoding="utf-8")
        self._diary.record(f"moved_to_{name}")
        log.info("the agent moved to %s", name)
        for other in self.bodies:
            await self.tell(other)

    async def tell(self, name: str) -> None:
        """Let one body know whether the agent is in it (when it connects, and after a move)."""
        away = name != self.where
        await self.bodies[name].send({"t": "away", "on": away, "text": WHERE_IT_WENT[self.where] if away else ""})

    def _load(self) -> str:
        try:
            where = json.loads(self._path.read_text(encoding="utf-8"))["where"]
        except (OSError, ValueError, KeyError, TypeError):
            return HOME
        return where if where in self.bodies else HOME
