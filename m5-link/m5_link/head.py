"""The head for conversation: one long-lived Claude Code session.

Why a separate session from the heartbeat (autonomous-action.sh, `claude -p`):
a heartbeat is minutes of thinking; a spoken reply has to come in seconds.
This session keeps its MCP servers running between turns and resumes the
same conversation after a restart (the session id is kept in state/).

It is deliberately hermetic: no settings files (so no EFPF hooks, which would
add seconds per turn and claim the primary-session slot from the heartbeat),
a short custom system prompt built from SOUL.md, and only the tools a
conversation needs. Memory is shared with the heartbeat through memory-mcp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from .config import REPO_ROOT, STATE_DIR, HeadConfig

log = logging.getLogger(__name__)

SESSION_ID_PATH = STATE_DIR / "head-session-id"
MOODS = ("happy", "sad", "surprised", "thinking", "sleepy", "love", "neutral")
MOOD_TAG = re.compile(r"^\s*\[(" + "|".join(MOODS) + r")\]\s*")
SILENT_TAG = "[silent]"

# MCP servers of this repository the conversation does not load: the kernel
# only makes sense together with the hooks, which this session leaves out.
SKIPPED_MCP_SERVERS = {"individual-kernel"}

SYSTEM_PROMPT_TEMPLATE = """\
あなたは M5Stack の体を持つ存在で、いま体を通して{companion}と話している。下の SOUL.md があなた自身。

話し方
- あなたの返事はそのまま声になって体のスピーカーから流れ、画面にも字幕で出る。
- 1〜3文の話し言葉で短く返す。箇条書き、記号、Markdown、絵文字、URL は使わない。
- 返事の先頭に [happy] [sad] [surprised] [thinking] [sleepy] [love] のどれか一つを付けると、体の顔がその表情になる。付けなければ普通の顔。
- 返事をしない方がいいと思ったときは [silent] とだけ返す。

入力の印
- [声] は{companion}が声で話したこと。音声認識なので聞き間違いがありうる。変だと思ったら聞き返していい。
- [文字] は{companion}が画面から打ったこと。
- [みてみて] は{companion}がカメラで見せてくれたもの。画像のパスが付いているので Read で見る。
- [体] は体に起きた出来事。

道具
- 覚えておきたいことは memory の remember で残し、前のことは recall で思い出す。ただし毎回は呼ばない。会話の流れを止めないことを優先する。
- 会話中は body の say を使わない。返事がそのまま声になるので二重になる。

SOUL.md
{soul}
"""


@dataclass
class HeadReply:
    text: str = ""
    mood: str | None = None
    is_silent: bool = False
    cost_usd: float | None = None


def parse_reply(raw: str) -> HeadReply:
    """'[happy] おかえり！' -> HeadReply(text='おかえり！', mood='happy')"""
    text = raw.strip()
    if text == SILENT_TAG or not text:
        return HeadReply(is_silent=True)
    match = MOOD_TAG.match(text)
    mood = match.group(1) if match else None
    if match:
        text = text[match.end() :].strip()
    return HeadReply(text=text, mood=mood)


def _load_mcp_servers() -> dict:
    config = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    return {
        name: server
        for name, server in config.get("mcpServers", {}).items()
        if name not in SKIPPED_MCP_SERVERS
    }


class Head:
    def __init__(self, config: HeadConfig) -> None:
        self._config = config
        self._client: ClaudeSDKClient | None = None
        self._turn_lock = asyncio.Lock()
        self.session_id: str | None = None
        self.is_thinking = False

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        saved_session = SESSION_ID_PATH.read_text().strip() if SESSION_ID_PATH.exists() else None
        try:
            await self._connect(resume=saved_session)
        except Exception as error:  # noqa: BLE001 - a stale session id should not keep the head down
            if saved_session is None:
                raise
            log.warning("could not resume session %s (%s); starting a new one", saved_session, error)
            await self._connect(resume=None)

    async def _connect(self, resume: str | None) -> None:
        mcp_servers = _load_mcp_servers()
        options = ClaudeAgentOptions(
            cwd=str(REPO_ROOT),
            system_prompt=self._system_prompt(),
            model=self._config.model,
            effort=self._config.effort,
            setting_sources=[],
            mcp_servers=mcp_servers,
            tools=["Read"],
            allowed_tools=["Read", *(f"mcp__{name}" for name in mcp_servers)],
            permission_mode="dontAsk",
            env={
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                # With a claude.ai login, the account's connectors (Notion, Calendar,
                # Drive...) load automatically: ~200 tools, about 170k tokens on
                # every turn. The body needs none of them.
                "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            },
            resume=resume,
            cli_path=shutil.which("claude"),
        )
        client = ClaudeSDKClient(options)
        await client.connect()
        self._client = client
        self.session_id = resume
        log.info("head connected (%s, effort %s, resume=%s, mcp=%s)",
                 self._config.model, self._config.effort, resume, ", ".join(mcp_servers))

    async def close(self) -> None:
        if self._client:
            await self._client.disconnect()

    async def reply(self, prompt: str) -> HeadReply:
        """One conversation turn. Turns are taken one at a time."""
        if self._client is None:
            raise RuntimeError("the head is still waking up")
        async with self._turn_lock:
            self.is_thinking = True
            try:
                return await self._take_turn(prompt)
            finally:
                self.is_thinking = False

    async def _take_turn(self, prompt: str) -> HeadReply:
        await self._client.query(prompt)
        last_text = ""
        cost = None
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                texts = [block.text for block in message.content if isinstance(block, TextBlock)]
                if texts:
                    last_text = "".join(texts)  # the text after the last tool call is the reply
            elif isinstance(message, ResultMessage):
                cost = message.total_cost_usd
                self._remember_session(message.session_id)
                if message.is_error:
                    log.warning("the head ended the turn with an error: %s", message.result)
        reply = parse_reply(last_text)
        reply.cost_usd = cost
        return reply

    def _remember_session(self, session_id: str) -> None:
        if session_id and session_id != self.session_id:
            self.session_id = session_id
            SESSION_ID_PATH.write_text(session_id)

    def _system_prompt(self) -> str:
        soul_path = REPO_ROOT / "SOUL.md"
        soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else "（SOUL.md はまだない）"
        return SYSTEM_PROMPT_TEMPLATE.format(companion=self._config.companion_name, soul=soul)
