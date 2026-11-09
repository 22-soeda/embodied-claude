"""Errands: slow work the conversation head hands to a background subagent.

A spoken reply has to come back in a second or two; looking something up on
the web or reading a long text takes much longer. So the head does not do such
work itself. It sends an "errand" (a Claude Code subagent that runs in the
background), says something like "見てくるね" at once, and goes on talking. When
the errand comes back, the session starts a turn of its own; the head's words
on it reach Conversation.tell_errand_report.

The design follows voice-agent frameworks that do the same (Pipecat's
asynchronous function calls, LiveKit's async tools, the Gemini Live API's
non-blocking functions) and DeepMind's Talker-Reasoner. This file keeps what
m5-link itself has to remember: which errands are out, and the reports the body
could not say because it was asleep.

When a report comes back, it is told the way the Gemini Live API calls
WHEN_IDLE or SILENT:
- the body is awake: it is said through the body once the turn in progress is
  over (the session takes turns one at a time, and the body finishes speaking
  first);
- the body is asleep or away: it is held, and the next conversation turn
  carries it as [言えなかった報告].
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Errand:
    task_id: str
    description: str
    started_at: float
    progress: str = ""  # the latest thing it said it was doing ("Searching for ...")


@dataclass
class HeldReport:
    text: str
    at: float


def should_hold(*, body_connected: bool, body_asleep: bool) -> bool:
    """Keep a report for the next conversation instead of saying it now."""
    return not body_connected or body_asleep


class ErrandBook:
    def __init__(self, held_path: Path) -> None:
        self._held_path = held_path
        self.running: dict[str, Errand] = {}

    # ------------------------------------------------------------ errands out

    def started(self, task_id: str, description: str, at: float | None = None) -> None:
        self.running[task_id] = Errand(task_id, description, at or time.time())

    def progressed(self, task_id: str, description: str) -> None:
        if task_id in self.running:
            self.running[task_id].progress = description

    def finished(self, task_id: str) -> Errand | None:
        return self.running.pop(task_id, None)

    def running_list(self) -> list[dict]:
        return [asdict(errand) for errand in self.running.values()]

    # ------------------------------------------------------------ held reports

    def hold(self, text: str, at: float | None = None) -> None:
        held = self._load_held()
        held.append(HeldReport(text, at or time.time()))
        self._held_path.parent.mkdir(parents=True, exist_ok=True)
        self._held_path.write_text(json.dumps([asdict(report) for report in held], ensure_ascii=False),
                                   encoding="utf-8")

    def held_notes(self) -> tuple[str, int]:
        """The held reports as lines for the head, and how many there are (to clear once read)."""
        held = self._load_held()
        lines = [f"{datetime.fromtimestamp(report.at):%m/%d %H:%M}「{report.text}」" for report in held]
        return "\n".join(lines), len(held)

    def clear_held(self, count: int) -> None:
        """Forget the first `count` held reports: the head has read them."""
        rest = self._load_held()[count:]
        if rest:
            self._held_path.write_text(json.dumps([asdict(report) for report in rest], ensure_ascii=False),
                                       encoding="utf-8")
        else:
            self._held_path.unlink(missing_ok=True)

    def _load_held(self) -> list[HeldReport]:
        try:
            return [HeldReport(**raw) for raw in json.loads(self._held_path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            return []
