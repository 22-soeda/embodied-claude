"""Settings for m5-link, read from m5-link/m5-link.toml (ignored by Git).

The file is created on first run with a random token, so the body has a
secret to present and nothing private ever lands in the public fork.
"""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
M5_LINK_DIR = PACKAGE_DIR.parent
REPO_ROOT = M5_LINK_DIR.parent
CONFIG_PATH = M5_LINK_DIR / "m5-link.toml"
STATE_DIR = M5_LINK_DIR / "state"

DEFAULT_CONFIG_TEXT = """\
# m5-link settings. This file is ignored by Git: it holds the body's token.

[server]
host = "0.0.0.0"   # the body connects from the LAN (or through Tailscale Funnel)
port = 8790
token = "{token}"  # the body sends it as ?token=; also unlocks the web page off this PC

[head]
model = "sonnet"   # conversation turns; short and fast matters more than depth
effort = "low"
companion_name = "添田"
day_starts_at = 4  # the conversation session is closed and started afresh at this hour; memory carries over
max_turns = 6      # model calls allowed for one reply, tool calls included

[ears]
# "auto" = large-v3-turbo on an NVIDIA GPU, small on the CPU otherwise.
model = "auto"
device = "auto"    # "cuda" or "cpu" to force one
language = "ja"

[mouth]
url = "http://127.0.0.1:10101"   # AivisSpeech Engine (VOICEVOX-compatible API)
engine_path = "~/AivisSpeech-Engine/Windows-x64/run.exe"  # started if not running
speaker = 0        # style id; 0 = the first style the engine lists
speed = 1.05

[mouth.readings]
# Words the engine reads wrong, put into its user dictionary at start.
# 表記 = "カタカナ" (flat accent), or 表記 = ["カタカナ", where the pitch falls]
# "東海林" = "ショウジ"

[heartbeat]
enabled = true     # run autonomous-action.sh on a schedule, instead of Task Scheduler
interval_minutes = 30   # as PetitOnes; the script still thins beats outside active hours

[nightly]
# As PetitOnes does it: a diary page from the day's memories, then the memory sleep.
diary_at = "23:50"
memory_sleep_at = "04:05"   # after the conversation session is closed at day_starts_at
wake_at = ""                # "07:00" to wake the body if the head did not; PetitOnes leave waking to the head

[backup]
# The agent's own files and databases, zipped every night. Somewhere off this PC's
# disk is best, e.g. a OneDrive folder: "~/OneDrive/embodied-claude-backup".
dir = ""                    # "" to turn off
at = "04:30"                # after the memory sleep
keep = 14                   # newest zips kept
"""


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8790
    token: str = ""


@dataclass(frozen=True)
class HeadConfig:
    model: str = "sonnet"
    effort: str = "low"
    companion_name: str = "あなた"
    day_starts_at: int = 4
    max_turns: int = 6


@dataclass(frozen=True)
class EarsConfig:
    model: str = "auto"
    device: str = "auto"
    language: str = "ja"


@dataclass(frozen=True)
class MouthConfig:
    url: str = "http://127.0.0.1:10101"
    engine_path: str = ""
    speaker: int = 0
    speed: float = 1.0
    readings: dict = field(default_factory=dict)  # surface -> "カタカナ" or ["カタカナ", accent]


@dataclass(frozen=True)
class HeartbeatConfig:
    enabled: bool = True
    interval_minutes: int = 30


@dataclass(frozen=True)
class NightlyConfig:
    diary_at: str = "23:50"         # "" to turn off
    memory_sleep_at: str = "04:05"  # "" to turn off
    wake_at: str = ""               # e.g. "07:00"; off by default, as in PetitOnes


@dataclass(frozen=True)
class BackupConfig:
    dir: str = ""      # "" to turn off
    at: str = "04:30"
    keep: int = 14


@dataclass(frozen=True)
class LinkConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    head: HeadConfig = field(default_factory=HeadConfig)
    ears: EarsConfig = field(default_factory=EarsConfig)
    mouth: MouthConfig = field(default_factory=MouthConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    nightly: NightlyConfig = field(default_factory=NightlyConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)


def load_config(path: Path = CONFIG_PATH) -> LinkConfig:
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_TEXT.format(token=secrets.token_urlsafe(18)), encoding="utf-8")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return LinkConfig(
        server=ServerConfig(**raw.get("server", {})),
        head=HeadConfig(**raw.get("head", {})),
        ears=EarsConfig(**raw.get("ears", {})),
        mouth=MouthConfig(**raw.get("mouth", {})),
        heartbeat=HeartbeatConfig(**raw.get("heartbeat", {})),
        nightly=NightlyConfig(**raw.get("nightly", {})),
        backup=BackupConfig(**raw.get("backup", {})),
    )
