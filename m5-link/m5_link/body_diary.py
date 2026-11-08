"""What happened to the body while nobody was talking, kept for the head to read later.

Being picked up, shaken, someone coming near, the battery running low: the
body reacts on the spot by itself (a surprised face), and m5-link only writes
the event down here. The head is not woken for it. The next time the head
speaks - a conversation turn or a heartbeat - it is handed the unread part as
a few lines, and decides itself whether to mention it.

PetitOnes' experience-daemon works the same way: record without calling the
model, hand over a digest at the next wake-up.

What the heartbeat says out loud through the body is written down here too
("said", with the words). The conversation runs in another session, so
without this, a reply to "そういえば…" from the body reached a head that did
not know anything had been said.

Two sessions read the diary - the conversation and the heartbeat - and each
keeps its own read mark: the heartbeat reading first must not hide from the
conversation what the body said a minute ago.

    state/body-diary/YYYYMMDD.jsonl            one event per line, only ever appended
    state/body-diary/read-until-<reader>       time of the newest event that reader was given
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Event kinds worth telling the head about, and how it reads them.
# connected / disconnected / mitemite are left out: the first two are m5-link's
# own business, and a "みてみて" picture already goes to the head as a turn.
DESCRIPTIONS = {
    "picked_up": "持ち上げられた",
    "shaken": "揺さぶられた",
    "near": "近くに誰か来た",
    "poked": "寝ているところをつつかれた",
    "battery_low": "電池が少なくなった",
    "said": "自分から体の声で話しかけた",  # with the words; never merged with another
}
# Repeats of one kind closer together than this become one line: "持ち上げられた（3 回）".
SAME_STRETCH_GAP_SECS = 10 * 60
MAX_DIGEST_LINES = 12
# Unread events older than this are not handed over any more.
LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class DiaryEntry:
    kind: str
    at: float  # unix seconds
    text: str = ""  # the words, for "said"


@dataclass
class Stretch:
    """One or more events of the same kind close together."""

    kind: str
    first_at: float
    last_at: float
    count: int = 1
    text: str = ""


class BodyDiary:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def record(self, kind: str, at: float | None = None, text: str = "") -> None:
        if kind not in DESCRIPTIONS:
            return
        at = time.time() if at is None else at
        line = {"at": round(at, 3), "kind": kind} | ({"text": text} if text else {})
        self._directory.mkdir(parents=True, exist_ok=True)
        with self._day_file(datetime.fromtimestamp(at)).open("a", encoding="utf-8") as file:
            file.write(json.dumps(line, ensure_ascii=False) + "\n")

    def unread(self, reader: str) -> tuple[str, float]:
        """The digest this reader has not been given yet, and the time of its newest event."""
        read_until = self.read_until(reader)
        since = max(read_until, time.time() - LOOKBACK_DAYS * 86400)
        entries = [entry for entry in self._entries_since(since) if entry.at > read_until]
        if not entries:
            return "", read_until
        return format_digest(entries), entries[-1].at

    def mark_read(self, reader: str, until: float) -> None:
        """Move this reader's mark forward (never back)."""
        if until <= self.read_until(reader):
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        self._read_until_path(reader).write_text(f"{until:.3f}", encoding="utf-8")

    def read_until(self, reader: str) -> float:
        try:
            return float(self._read_until_path(reader).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0.0

    def _read_until_path(self, reader: str) -> Path:
        return self._directory / f"read-until-{reader}"

    def recent(self, hours: float) -> str:
        """The digest of the last few hours, read or not (for the body MCP and the web page)."""
        entries = self._entries_since(time.time() - hours * 3600)
        return format_digest(entries) if entries else ""

    def _entries_since(self, since: float) -> list[DiaryEntry]:
        entries = []
        day = datetime.fromtimestamp(since).date()
        while day <= datetime.now().date():
            path = self._day_file(day)
            if path.exists():
                entries.extend(_read_entries(path))
            day += timedelta(days=1)
        return sorted((entry for entry in entries if entry.at >= since), key=lambda entry: entry.at)

    def _day_file(self, day) -> Path:
        return self._directory / f"{day:%Y%m%d}.jsonl"


def _read_entries(path: Path) -> list[DiaryEntry]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
            entries.append(DiaryEntry(kind=raw["kind"], at=float(raw["at"]), text=raw.get("text", "")))
        except (ValueError, KeyError, TypeError):
            continue  # a half-written last line after a crash
    return entries


def group_into_stretches(entries: list[DiaryEntry]) -> list[Stretch]:
    """Merge events of one kind that follow each other within SAME_STRETCH_GAP_SECS.

    Kinds are merged separately, so "near" events in between do not split a
    run of "picked_up" (lifting the body also brings a hand near its screen).
    Entries with words ("said") always stand alone.
    """
    open_stretch: dict[str, Stretch] = {}
    stretches: list[Stretch] = []
    for entry in sorted(entries, key=lambda entry: entry.at):
        if entry.text:
            stretches.append(Stretch(entry.kind, entry.at, entry.at, text=entry.text))
            continue
        current = open_stretch.get(entry.kind)
        if current and entry.at - current.last_at <= SAME_STRETCH_GAP_SECS:
            current.last_at = entry.at
            current.count += 1
            continue
        current = Stretch(entry.kind, entry.at, entry.at)
        open_stretch[entry.kind] = current
        stretches.append(current)
    return sorted(stretches, key=lambda stretch: stretch.first_at)


def format_digest(entries: list[DiaryEntry], now: datetime | None = None) -> str:
    """Lines like "20:11〜20:16 持ち上げられた（3 回）", oldest first, at most MAX_DIGEST_LINES."""
    now = now or datetime.now()
    stretches = group_into_stretches(entries)
    lines = [_describe(stretch, now) for stretch in stretches[-MAX_DIGEST_LINES:]]
    left_out = len(stretches) - len(lines)
    if left_out > 0:
        lines.insert(0, f"（それより前にも {left_out} 件）")
    return "\n".join(lines)


def _describe(stretch: Stretch, now: datetime) -> str:
    first = datetime.fromtimestamp(stretch.first_at)
    day = "" if first.date() == now.date() else f"{first:%m/%d} "
    what = DESCRIPTIONS.get(stretch.kind, stretch.kind)
    if stretch.text:
        return f"{day}{first:%H:%M} {what}「{stretch.text}」"
    if stretch.count == 1:
        return f"{day}{first:%H:%M} {what}"
    last = datetime.fromtimestamp(stretch.last_at)
    return f"{day}{first:%H:%M}〜{last:%H:%M} {what}（{stretch.count} 回）"
