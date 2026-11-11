"""The body's desires, computed by desire-system the way PetitOnes does it.

Every five minutes m5-link writes what the body is going through to
state/body-now.json and runs desire-system's updater with the body's set of
desires (m5-link/desire-config.json). A desire's level comes from memory - how
long since the head last did what meets it - and the body moves it: a low
battery halves every desire, a touch in the last 20 minutes halves wanting to
talk. Nothing goes down because a heartbeat was told about it; only doing it
counts.

The desires are the agent's own: configure_v0.py copies the template
(m5-link/desire-config.json) to desire-config.json at the repository root, and
the heartbeat may change that copy - what it wants, how fast, and new wants -
without asking, as PetitOnes' characters may change theirs. desire-system is
given the copy first and the template second, so a copy it cannot read leaves
the body on the template. What is wrong with the copy (unreadable, or a name
other parts read is gone) goes into the heartbeat's digest, so the agent can
mend it.

A conversation through the body is talking with the companion: after a turn
the person started, m5-link records miss_companion as met. At most every 30
minutes, since each record is a row in the memory store.

Before each heartbeat, the desires that have grown strong are written to
state/desire-digest.txt, which autonomous-action.sh reads (DESIRE_DIGEST_FILE).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from .body import Body
from .body_diary import BodyDiary
from .config import M5_LINK_DIR, REPO_ROOT, STATE_DIR

log = logging.getLogger(__name__)

DESIRE_CONFIG_PATH = REPO_ROOT / "desire-config.json"  # the agent's own copy, ignored by Git
TEMPLATE_DESIRE_CONFIG_PATH = M5_LINK_DIR / "desire-config.json"
# Read by name elsewhere: individual-kernel and interaction-orchestrator (the
# first three), and m5-link itself (talking, sleeping).
NAMES_READ_ELSEWHERE = ("miss_companion", "observe_room", "browse_curiosity", "want_to_sleep")
BODY_STATE_PATH = STATE_DIR / "body-now.json"
DESIRES_PATH = Path.home() / ".claude" / "desires.json"  # desire-system's default; the kernel reads it too
UPDATE_INTERVAL_SECS = 5 * 60
UPDATER_TIMEOUT_SECS = 120
TALK_RECORD_GAP_SECS = 30 * 60
STRONG_LEVEL = 0.7  # PetitOnes: "act immediately on any desire with level >= 0.7"
TALKING = "miss_companion"
SLEEPING = "want_to_sleep"
CONTACT_KINDS = {"picked_up", "shaken", "poked"}


class Desires:
    def __init__(self, body: Body, diary: BodyDiary, companion_name: str) -> None:
        self._body = body
        self._diary = diary
        self._companion_name = companion_name
        self._labels: dict[str, str] = {}
        self._config_problems: list[str] = []
        self._last_config_problems: list[str] = []
        self._read_config()
        self._update_lock = asyncio.Lock()
        self._last_talk_record = float("-inf")  # monotonic seconds
        self._last_error = ""
        self._last_strong: list[str] = []

    async def run_forever(self) -> None:
        while True:
            await self.update()
            await asyncio.sleep(UPDATE_INTERVAL_SECS)

    async def slept(self, reason: str) -> None:
        """The body has fallen asleep: record it as meeting want_to_sleep."""
        why = "電池が減ったので" if reason == "battery" else ""
        await self.update(satisfy=SLEEPING, summary=f"{why}体が眠った")

    async def talked(self, how: str) -> None:
        """The companion talked with the body: record it as meeting miss_companion (rate-limited)."""
        if time.monotonic() - self._last_talk_record < TALK_RECORD_GAP_SECS:
            return
        self._last_talk_record = time.monotonic()
        await self.update(satisfy=TALKING, summary=f"体越しに{how}で話しかけてくれた")

    async def update(self, satisfy: str | None = None, summary: str = "") -> bool:
        """Write the body's state and run desire-system's updater once."""
        async with self._update_lock:
            self._read_config()  # the heartbeat may have changed it since
            self._write_body_state()
            command = [shutil.which("uv") or "uv", "run", "--package", "desire-system", "--no-sync",
                       "desire-updater"]
            if satisfy:
                command += ["--satisfy", satisfy, "--summary", summary]
            process = await asyncio.create_subprocess_exec(
                *command, cwd=REPO_ROOT, env=self._environment(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=0x08000000 if sys.platform == "win32" else 0,  # no console window
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), UPDATER_TIMEOUT_SECS)
            except TimeoutError:
                process.kill()
                self._report_error("the desire updater ran over two minutes")
                return False
        told = stderr.decode("utf-8", "replace")
        if process.returncode != 0:
            self._report_error(told.strip()[-500:])
            return False
        self._config_problems += [line.removeprefix("[desire-updater] ") for line in told.splitlines()
                                  if line.startswith("[desire-updater]")]
        self._report_config_problems()
        self._last_error = ""
        if satisfy:
            log.info("recorded %s as met: %s", satisfy, summary)
        self._report_strong_changes()
        return True

    def levels(self) -> list[dict]:
        """Every desire with its label and level, strongest first ([] before the first run)."""
        try:
            snapshot = json.loads(DESIRES_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        dominant = snapshot.get("dominant")
        levels = [
            {"name": name, "label": self._labels.get(name, name), "level": float(level),
             "dominant": name == dominant}
            for name, level in snapshot.get("desires", {}).items()
        ]
        return sorted(levels, key=lambda desire: -desire["level"])

    def strong_lines(self) -> list[str]:
        """'添田に会いたい（0.72）' for each desire at or over STRONG_LEVEL."""
        return [f"{d['label']}（{d['level']:.2f}）" for d in self.levels() if d["level"] >= STRONG_LEVEL]

    def write_digest(self, path: Path) -> None:
        """The strong desires, and what is wrong with the agent's desires, for the heartbeat; no file when neither."""
        lines = self.strong_lines() + [f"（欲求の設定）{problem}" for problem in self._config_problems]
        if lines:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def _write_body_state(self) -> None:
        state = self._body.state if self._body.is_connected else {}
        body_now = {
            "updated_at": time.time(),
            "connected": self._body.is_connected,
            "battery": state.get("battery"),
            "charging": state.get("charging"),
            "last_contact_at": self._diary.last_event_at(CONTACT_KINDS),
        }
        STATE_DIR.mkdir(exist_ok=True)
        BODY_STATE_PATH.write_text(json.dumps(body_now), encoding="utf-8")

    def _environment(self) -> dict[str, str]:
        environment = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
        environment |= {
            "DESIRE_CONFIG_FILE": os.pathsep.join([str(DESIRE_CONFIG_PATH), str(TEMPLATE_DESIRE_CONFIG_PATH)]),
            "DESIRE_BODY_STATE_FILE": str(BODY_STATE_PATH),
            "COMPANION_NAME": self._companion_name,
            "PYTHONIOENCODING": "utf-8",
        }
        return environment

    def _read_config(self) -> None:
        """Labels from the agent's copy (the template when the copy cannot be read), and names gone from it."""
        try:
            self._labels = _labels(DESIRE_CONFIG_PATH, self._companion_name)
            missing = [name for name in NAMES_READ_ELSEWHERE if name not in self._labels]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):  # desire-system tells what is wrong
            self._labels = _labels(TEMPLATE_DESIRE_CONFIG_PATH, self._companion_name)
            missing = []
        self._config_problems = [
            f"{DESIRE_CONFIG_PATH.name} に {name} が無い。ほかの仕組みが名前で読むので、この名前は残してほしい"
            for name in missing
        ]

    def _report_config_problems(self) -> None:
        if self._config_problems != self._last_config_problems:
            log.warning("the agent's desires: %s", " / ".join(self._config_problems) or "fine again")
        self._last_config_problems = list(self._config_problems)

    def _report_error(self, error: str) -> None:
        if error != self._last_error:  # once per kind of failure, not every five minutes
            log.warning("the desire updater failed: %s", error)
        self._last_error = error

    def _report_strong_changes(self) -> None:
        strong = self.strong_lines()
        names = [line.split("（")[0] for line in strong]
        if names != self._last_strong:
            log.info("strong desires now: %s", "、".join(strong) or "none")
        self._last_strong = names


def _labels(path: Path, companion_name: str) -> dict[str, str]:
    desires = json.loads(path.read_text(encoding="utf-8"))["desires"]
    return {name: spec.get("label", name).replace("{companion}", companion_name)
            for name, spec in desires.items()}
