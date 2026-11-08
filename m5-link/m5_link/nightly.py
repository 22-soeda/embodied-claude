"""Nightly upkeep, the way PetitOnes does it: a diary page at 23:50, the memory sleep at 04:05.

Diary (adapted from PetitOnes/petit-ones dashboard/generate_diary.py and
generate_diary_summary, MIT License): the day's memories, and what happened
to the body that day, go to a one-off model call with SOUL.md, which writes
two or three sentences in the agent's own voice into diary/YYYY-MM-DD.txt.
If the agent already wrote that day's page itself in a heartbeat, its page is
kept. Then diary_summary.md - the newest page in full, and the first line of
the seven before it - is rebuilt; the heartbeat and the conversation head read
it every time, so a new day's session knows the last few days.

Backup: at 04:30, after the sleep, the agent's own files and databases are zipped
into [backup] dir (backup.py).

Morning: at 07:00 the body is woken, in case the head put it to sleep with the
body MCP's sleep() and did not wake it.

Sleep: memory-mcp's `memory_mcp.sleep` (ported from PetitOnes/m5-petit-memory)
merges near-duplicate memories, lowers the importance of fading ones and
forgets trivial old ones, never touching important, emotional or first-time
memories. It runs after the conversation session has been closed for the day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from .body import Body
from .body_diary import BodyDiary
from .backup import backup_folder, make_backup
from .config import REPO_ROOT, BackupConfig, HeadConfig, NightlyConfig

log = logging.getLogger(__name__)

DIARY_DIR = REPO_ROOT / "diary"
DIARY_SUMMARY_PATH = REPO_ROOT / "diary_summary.md"
SOUL_PATH = REPO_ROOT / "SOUL.md"
MEMORY_DB_PATH = Path(os.getenv("MEMORY_DB_PATH", str(Path.home() / ".claude" / "memories" / "memory.db")))
SUMMARY_PAST_PAGES = 7
SUMMARY_LINE_CHARS = 80
SLEEP_TIMEOUT_SECS = 15 * 60
DATE_STEM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DESIRE_RECORD = re.compile(r"^\[desire:[a-z_]+\]\s*")  # desire-system's satisfaction rows

DIARY_PROMPT = """\
あなたは下の SOUL.md の存在です。

{soul}

{day}の記憶一覧:
{memories}
{body_section}
この日を自分の口調で2〜3文の日記にまとめて。余計な前置きなしで日記の文章だけ書いて。"""


class Nightly:
    def __init__(self, config: NightlyConfig, head: HeadConfig, body_diary: BodyDiary,
                 body: Body | None = None, backup: BackupConfig | None = None) -> None:
        self._config = config
        self._backup = backup or BackupConfig()
        self._head = head
        self._body_diary = body_diary
        self._body = body

    async def run_forever(self) -> None:
        jobs = [(self._config.diary_at, "diary", self.write_diary),
                (self._config.memory_sleep_at, "memory sleep", self.sleep),
                (self._config.wake_at, "body wake", self.wake_body),
                (self._backup.at if self._backup.dir else "", "backup", self.back_up)]
        await asyncio.gather(*(_every_day_at(at, name, job) for at, name, job in jobs if at))

    # ------------------------------------------------------------ diary

    async def write_diary(self, day: date | None = None) -> str:
        """Write the diary page for ``day`` (today by default) unless one exists; rebuild the summary."""
        day = day or date.today()
        existing = _diary_page(day)
        if existing:
            log.info("diary %s was written by the agent itself; keeping it", day)
            update_diary_summary()
            return existing.read_text(encoding="utf-8").strip()

        memories = day_memories(day)
        if not memories:
            log.info("diary %s: no memories that day; nothing written", day)
            return ""
        body_notes = self._body_diary.recent(_hours_since_midnight()) if day == date.today() else ""
        prompt = DIARY_PROMPT.format(
            soul=SOUL_PATH.read_text(encoding="utf-8") if SOUL_PATH.exists() else "",
            day=day.isoformat(),
            memories="\n".join(f"- {memory}" for memory in memories),
            body_section=f"\n{day.isoformat()}に体に起きたこと:\n{body_notes}\n" if body_notes else "",
        )
        page = await self._write_in_own_voice(prompt)
        if not page:
            log.warning("diary %s: the model returned nothing", day)
            return ""
        DIARY_DIR.mkdir(exist_ok=True)
        (DIARY_DIR / f"{day.isoformat()}.txt").write_text(page + "\n", encoding="utf-8")
        update_diary_summary()
        log.info("diary %s written from %d memories: %s", day, len(memories), page[:80])
        return page

    async def _write_in_own_voice(self, prompt: str) -> str:
        options = ClaudeAgentOptions(
            system_prompt="日記を書く。",
            model=self._head.model,
            effort="low",
            setting_sources=[],
            tools=[],
            max_turns=1,
            permission_mode="dontAsk",
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
            cli_path=shutil.which("claude"),
        )
        texts: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                texts += [block.text for block in message.content if isinstance(block, TextBlock)]
        return "".join(texts).strip()

    # ------------------------------------------------------------ backup

    async def back_up(self) -> Path | None:
        """Zip the agent's own files and databases into [backup] dir (see backup.py)."""
        destination = backup_folder(self._backup)
        if destination is None:
            log.info("no backup folder set ([backup] dir); nothing backed up")
            return None
        return await asyncio.to_thread(make_backup, destination, self._backup.keep)

    # ------------------------------------------------------------ morning

    async def wake_body(self) -> None:
        """Wake the body in the morning, in case the head put it to sleep and did not wake it."""
        if self._body is not None and await self._body.send({"t": "wake"}):
            log.info("woke the body for the morning")

    # ------------------------------------------------------------ sleep

    async def sleep(self) -> dict | None:
        """Run memory-mcp's nightly sleep once; returns its counts."""
        environment = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
        environment["PYTHONIOENCODING"] = "utf-8"
        process = await asyncio.create_subprocess_exec(
            shutil.which("uv") or "uv", "run", "--package", "memory-mcp", "--no-sync",
            "python", "-m", "memory_mcp.sleep",
            cwd=REPO_ROOT, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # no console window
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), SLEEP_TIMEOUT_SECS)
        except TimeoutError:
            process.kill()
            log.error("the memory sleep ran over %d minutes", SLEEP_TIMEOUT_SECS // 60)
            return None
        if process.returncode != 0:
            log.error("the memory sleep failed: %s", stderr.decode("utf-8", "replace").strip()[-500:])
            return None
        result = json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
        log.info("memory sleep: %s (protected %s)", result["counts"], result["protected"])
        for entry in result["forgotten"]:
            log.info("  forgot: %s", entry["content"])
        return result["counts"]


# ---------------------------------------------------------------- helpers


def day_memories(day: date) -> list[str]:
    """The contents of the memories made on ``day`` (local time), oldest first."""
    if not MEMORY_DB_PATH.exists():
        return []
    db = sqlite3.connect(f"{MEMORY_DB_PATH.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT timestamp, content FROM memories").fetchall()
    finally:
        db.close()
    # Sorted by the moment, not the stored text: "…T03:00+00:00" is noon in Tokyo.
    that_day = sorted(((when, content) for stamp, content in rows
                       if (when := _local_moment(stamp)) is not None and when.date() == day),
                      key=lambda pair: pair[0])  # ties keep the store's order
    return [DESIRE_RECORD.sub("", content) for _, content in that_day]


def update_diary_summary() -> None:
    """diary_summary.md: the newest page in full, then one line from each of the pages before it."""
    pages = sorted((page for page in DIARY_DIR.glob("*.*")
                    if page.suffix in (".txt", ".md") and DATE_STEM.match(page.stem)),
                   key=lambda page: page.stem, reverse=True)
    if not pages:
        return
    newest, older = pages[0], pages[1 : SUMMARY_PAST_PAGES + 1]
    lines = [f"## 最新の日記（{newest.stem}）", "", newest.read_text(encoding="utf-8").strip(), ""]
    if older:
        lines.append("## 直近の日記")
        for page in older:
            text = page.read_text(encoding="utf-8").strip()
            first_line = text.splitlines()[0] if text else ""
            if len(first_line) > SUMMARY_LINE_CHARS:
                first_line = first_line[:SUMMARY_LINE_CHARS] + "…"
            lines.append(f"- {page.stem}: {first_line}")
    DIARY_SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _diary_page(day: date) -> Path | None:
    for suffix in (".txt", ".md"):
        page = DIARY_DIR / f"{day.isoformat()}{suffix}"
        if page.exists():
            return page
    return None


def _local_moment(stamp: str) -> datetime | None:
    """A stored timestamp as naive local time: naive ones are local already, aware ones are converted."""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def _hours_since_midnight() -> float:
    now = datetime.now()
    return (now - datetime.combine(now.date(), time())).total_seconds() / 3600


async def _every_day_at(at: str, name: str, job) -> None:
    hour, minute = (int(part) for part in at.split(":"))
    while True:
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            await job()
        except Exception:  # noqa: BLE001 - one bad night must not stop the next
            log.exception("the nightly %s failed", name)
