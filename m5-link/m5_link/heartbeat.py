"""Run the autonomous heartbeat (autonomous-action.sh) on a schedule.

This replaces the Task Scheduler entry from scripts/windows/register-heartbeat.ps1
when m5-link is running: one process to start at logon instead of two. The
heartbeat itself is unchanged - a separate `claude -p` with the EFPF hooks -
so it keeps the primary-session slot to itself; the conversation session in
head.py runs without hooks and never competes for it.

Beats are aligned to the clock (:00 for 60 minutes), because the script's own
schedule control only lets off-peak beats through at minute 00.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import REPO_ROOT, HeartbeatConfig

log = logging.getLogger(__name__)

GIT_BASH = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
BEAT_TIMEOUT_S = 30 * 60


class Heartbeat:
    def __init__(self, config: HeartbeatConfig) -> None:
        self._config = config
        self.last_started: datetime | None = None
        self.last_exit_code: int | None = None
        self.is_running = False

    def next_beat_after(self, now: datetime) -> datetime:
        interval = self._config.interval_minutes
        minutes_since_hour = now.minute + now.second / 60
        next_slot = (int(minutes_since_hour // interval) + 1) * interval
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=next_slot)

    async def run_forever(self) -> None:
        if not self._config.enabled:
            log.info("heartbeat disabled in m5-link.toml")
            return
        while True:
            next_beat = self.next_beat_after(datetime.now())
            log.info("next heartbeat at %s", next_beat.strftime("%H:%M"))
            await asyncio.sleep((next_beat - datetime.now()).total_seconds() + 1)
            await self.beat()

    async def beat(self) -> None:
        if self.is_running:
            log.warning("the previous heartbeat is still running; skipping this one")
            return
        script = REPO_ROOT / "autonomous-action.sh"
        if not script.exists():
            log.error("autonomous-action.sh is missing; run scripts/windows/configure_v0.py")
            return

        self.is_running = True
        self.last_started = datetime.now()
        try:
            process = await asyncio.create_subprocess_exec(
                str(GIT_BASH), "-l", "-c", _posix_path(script),
                cwd=REPO_ROOT,
                env=_environment_without_claude_session(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,  # no console window
            )
            self.last_exit_code = await asyncio.wait_for(process.wait(), BEAT_TIMEOUT_S)
            log.info("heartbeat finished with exit code %s", self.last_exit_code)
        except TimeoutError:
            log.error("heartbeat ran over %d minutes; killing it", BEAT_TIMEOUT_S // 60)
            process.kill()
        finally:
            self.is_running = False

    def summary(self) -> dict:
        return {
            "enabled": self._config.enabled,
            "running": self.is_running,
            "last_started": self.last_started.isoformat() if self.last_started else None,
            "last_exit_code": self.last_exit_code,
            "next": self.next_beat_after(datetime.now()).isoformat() if self._config.enabled else None,
        }


def _posix_path(path: Path) -> str:
    """C:\\Users\\me\\x.sh -> /c/Users/me/x.sh, as Git Bash expects."""
    drive, rest = path.drive, path.as_posix()[len(path.drive):]
    return f"/{drive.rstrip(':').lower()}{rest}"


def _environment_without_claude_session() -> dict[str, str]:
    """`claude -p` refuses to start inside another Claude Code session's environment."""
    return {key: value for key, value in os.environ.items() if not key.startswith("CLAUDE")}
