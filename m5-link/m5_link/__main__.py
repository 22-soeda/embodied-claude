"""Start m5-link: `uv run --project m5-link python -m m5_link` from the repo root.

The web server comes up first, so the body can connect and show NO BRAIN ->
awake as soon as possible; the speech engine, speech recognition and the head
wake up in the background, each logging when it is ready.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
from logging.handlers import RotatingFileHandler

from aiohttp import web

from .body import Body
from .body_diary import BodyDiary
from .config import REPO_ROOT, STATE_DIR, load_config
from .desires import Desires
from .conversation import Conversation
from .ears import Ears
from .head import Head
from .heartbeat import Heartbeat
from .nightly import Nightly
from .mouth import Mouth
from .presence import Presence
from .server import build_app

log = logging.getLogger("m5_link")


def setup_logging() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(STATE_DIR / "m5-link.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    # One line per HTTP request from the model download and the speech engine
    # would bury the conversation.
    for noisy in ("aiohttp.access", "httpx", "httpx2", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def wake_up(name: str, starter) -> None:
    try:
        await starter()
    except Exception:  # noqa: BLE001 - one missing part must not take the others down
        log.exception("%s failed to start", name)


async def keep_head(head: Head) -> None:
    await wake_up("head", head.start)
    await head.run_day_turnover()


def code_version() -> str:
    """'9f3c2a1', or '9f3c2a1+changes' when tracked files differ from that commit."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    try:
        commit = git("rev-parse", "--short", "HEAD")
        changed = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if not commit:
        return "unknown"
    return f"{commit}+changes" if changed else commit


def lan_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outgoing interface
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


async def main() -> None:
    setup_logging()
    config = load_config()
    version = code_version()
    log.info("m5-link starting at %s", version)

    m5, phone = Body("M5"), Body("phone")
    diary = BodyDiary(STATE_DIR / "body-diary")
    presence = Presence({"m5": m5, "phone": phone}, STATE_DIR / "presence.json", diary)
    desires = Desires(m5, diary, config.head.companion_name)
    ears = Ears(config.ears)
    mouth = Mouth(config.mouth)
    head = Head(config.head)
    heartbeat = Heartbeat(config.heartbeat, diary, desires, config.head.companion_name)
    nightly = Nightly(config.nightly, config.head, diary, m5, config.backup)
    conversation = Conversation(presence, ears, head, mouth, diary, desires)

    runner = web.AppRunner(build_app(config, conversation, heartbeat, nightly, version))
    await runner.setup()
    await web.TCPSite(runner, config.server.host, config.server.port).start()

    address = lan_address()
    log.info("web page: http://127.0.0.1:%d/  (from other devices: http://%s:%d/?token=%s)",
             config.server.port, address, config.server.port, config.server.token)
    log.info("body url: ws://%s:%d/body  token: %s", address, config.server.port, config.server.token)

    background = [
        asyncio.create_task(wake_up("speech engine", mouth.start)),
        asyncio.create_task(wake_up("speech recognition", ears.load)),
        asyncio.create_task(keep_head(head)),
        asyncio.create_task(heartbeat.run_forever()),
        asyncio.create_task(desires.run_forever()),
        asyncio.create_task(nightly.run_forever()),
    ]
    try:
        await asyncio.gather(*background)
        await asyncio.Event().wait()
    finally:
        await head.close()
        await mouth.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
