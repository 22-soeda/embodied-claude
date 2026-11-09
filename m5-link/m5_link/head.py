"""The head for conversation: one long-lived Claude Code session per day.

Why a separate session from the heartbeat (autonomous-action.sh, `claude -p`):
a heartbeat is minutes of thinking; a spoken reply has to come in seconds.
This session keeps its MCP servers running between turns, which is what makes
a reply come back in one or two seconds.

It is deliberately hermetic: no settings files (so no EFPF hooks, which would
add seconds per turn and claim the primary-session slot from the heartbeat),
a short custom system prompt built from SOUL.md, and only the tools a
conversation needs. Memory is shared with the heartbeat through memory-mcp.

The session is closed every morning at day_starts_at (PetitOnes resets its
sessions daily too). Before closing, the head is asked to write down in
memory what it wants to keep; the new day's session starts with SOUL.md and
memory, not with yesterday's conversation. That keeps the context from growing
all day, and lets tool changes take effect - a resumed session keeps sending
the tool definitions it started with, even for tools since removed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from .config import REPO_ROOT, STATE_DIR, HeadConfig

log = logging.getLogger(__name__)

SESSION_STATE_PATH = STATE_DIR / "head-session.json"
MOODS = ("happy", "sad", "surprised", "thinking", "sleepy", "love", "neutral")
MOOD_TAG = re.compile(r"^\s*\[(" + "|".join(MOODS) + r")\]\s*")
SILENT_TAG = "[silent]"
# <br>, <br/>, </br>: the head sometimes ends a reply with one, and the voice read it out (2026-11-09).
LINE_BREAK_TAG = re.compile(r"<\s*/?\s*br\s*/?\s*>", re.IGNORECASE)

# Read: the "みてみて" pictures and its own reflections. WebSearch / WebFetch: from
# outside, as PetitOnes have them (books go through the reader MCP server).
BUILTIN_TOOLS = ["Read", "WebSearch", "WebFetch"]
# MCP servers of this repository the conversation does not load: the kernel
# only makes sense together with the hooks, which this session leaves out.
SKIPPED_MCP_SERVERS = {"individual-kernel"}
# A context bigger than this at start means something unexpected was loaded
# (on 2026-11-08 the claude.ai connectors put 173k tokens there).
CONTEXT_WARNING_TOKENS = 60_000
MCP_STARTUP_WAIT_SECS = 90

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
- [体の記録] は、前に話してから体に起きたこと（持ち上げられた、揺さぶられた、近くに誰か来た など）。気になれば話のついでに触れていい。触れなくてもいい。
- [体の記録] の「自分から体の声で話しかけた「…」」は、定期起床のときのあなたが体の声で{companion}に話しかけた言葉。この会話のあなたは覚えていないが、あなた自身が言ったこと。すぐあとの [声] は、たぶんその返事。
- [区切り] は一日の終わりの合図。

記憶
- この会話は毎朝 {day_starts_at} 時に区切られ、次の日のあなたには残らない。残るのは memory に書いたことと SOUL.md だけ。
- 名前や呼び方、約束、{companion}の好き嫌い、大事な出来事を知ったら、その場で memory の remember に残す。
- 前のことを聞かれたら recall で思い出す。それ以外で毎回は呼ばない。会話の流れを止めないことを優先する。

道具
- 会話中は body の say を使わない。返事がそのまま声になるので二重になる。
- 調べものは WebSearch と WebFetch、本や長い文章は reader の read_page で 1 ページずつ。返事が遅くなるので、会話中は頼まれたときか、どうしても要るときだけにする。

SOUL.md
{soul}
{reflection_index}{diary_summary}"""

CLOSE_THE_DAY_PROMPT = (
    "[区切り] 今日の会話はここで区切る。次の日のあなたは、この会話を覚えていない。"
    "持ち越したいこと（{companion}について知ったこと、約束、名前や呼び方、大事な出来事）が"
    "まだ memory に無ければ remember で残して。終わったら [silent] とだけ返して。"
)


@dataclass
class HeadReply:
    text: str = ""
    mood: str | None = None
    is_silent: bool = False
    tokens_read: int | None = None  # prompt tokens this reply took, cached or not


@dataclass
class SessionState:
    """What m5-link remembers about the head's session across restarts."""

    session_id: str | None = None
    day: str = ""          # conversation_day() the session belongs to
    tool_fingerprint: str = ""
    turns: int = 0         # replies so far; a session with none needs no closing

    @classmethod
    def load(cls) -> SessionState:
        try:
            return cls(**json.loads(SESSION_STATE_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        SESSION_STATE_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")


def parse_reply(raw: str) -> HeadReply:
    """'[happy] おかえり！' -> HeadReply(text='おかえり！', mood='happy')"""
    text = LINE_BREAK_TAG.sub(" ", raw).strip()
    if text == SILENT_TAG or not text:
        return HeadReply(is_silent=True)
    match = MOOD_TAG.match(text)
    mood = match.group(1) if match else None
    if match:
        text = text[match.end() :].strip()
    return HeadReply(text=text, mood=mood)


def conversation_day(now: datetime, day_starts_at: int) -> str:
    """The day a moment belongs to, when days start at day_starts_at: 03:00 on the 9th -> '…-08'."""
    return (now - timedelta(hours=day_starts_at)).date().isoformat()


def next_day_start(now: datetime, day_starts_at: int) -> datetime:
    start = now.replace(hour=day_starts_at, minute=0, second=0, microsecond=0)
    return start if start > now else start + timedelta(days=1)


def tool_fingerprint(mcp_server_names: list[str]) -> str:
    """Changes whenever the set of tools the head is given changes.

    A resumed session keeps sending the tool definitions it started with (to
    keep the prompt cache), so a changed tool set means a new session.
    """
    surface = {"builtin": BUILTIN_TOOLS, "mcp": sorted(mcp_server_names), "claudeai_connectors": False}
    return hashlib.sha1(json.dumps(surface, sort_keys=True).encode()).hexdigest()[:12]


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
        self._state = SessionState()
        self._background: set[asyncio.Task] = set()
        self.is_thinking = False
        self.mcp_status: list[dict] = []  # what the session reports for each MCP server
        self.context_tokens: int | None = None  # context size right after connecting

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    @property
    def session_id(self) -> str | None:
        return self._state.session_id

    # ------------------------------------------------------------ session life

    async def start(self) -> None:
        """Resume today's session if there is one with the same tools; otherwise start afresh."""
        saved = SessionState.load()
        fingerprint = tool_fingerprint(list(_load_mcp_servers()))
        if saved.session_id and saved.tool_fingerprint != fingerprint:
            log.info("the head's tools changed since session %s; starting a new session", saved.session_id)
        elif saved.session_id:
            try:
                await self._connect(saved)
            except Exception as error:  # noqa: BLE001 - a stale session must not keep the head down
                log.warning("could not resume session %s (%s); starting a new one", saved.session_id, error)
            else:
                if saved.day != self._today():
                    # m5-link was not running when the day turned: close yesterday now.
                    async with self._turn_lock:
                        await self._close_the_day()
                return
        await self._connect(self._new_state())

    async def run_day_turnover(self) -> None:
        """Every day at day_starts_at, close the conversation session and start a new one."""
        while True:
            wait = (next_day_start(datetime.now(), self._config.day_starts_at) - datetime.now()).total_seconds()
            await asyncio.sleep(wait + 1)
            async with self._turn_lock:  # waits for a conversation in progress to finish
                if self._client is not None and self._state.day != self._today():
                    await self._close_the_day()

    async def close(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def _close_the_day(self) -> None:
        """Let the head write down what it wants to keep, then start the new day's session.

        The caller holds the turn lock.
        """
        if self._state.turns > 0:
            prompt = CLOSE_THE_DAY_PROMPT.format(companion=self._config.companion_name)
            try:
                await self._take_turn(prompt)
                log.info("the head closed the day %s (session %s, %d replies)",
                         self._state.day, self._state.session_id, self._state.turns)
            except Exception:  # noqa: BLE001 - the new day starts even if the old one could not close
                log.exception("the head could not close the day; starting the new session anyway")
        await self.close()
        await self._connect(self._new_state())

    def _new_state(self) -> SessionState:
        return SessionState(day=self._today(), tool_fingerprint=tool_fingerprint(list(_load_mcp_servers())))

    def _today(self) -> str:
        return conversation_day(datetime.now(), self._config.day_starts_at)

    async def _connect(self, state: SessionState) -> None:
        mcp_servers = _load_mcp_servers()
        options = ClaudeAgentOptions(
            cwd=str(REPO_ROOT),
            system_prompt=self._system_prompt(),
            model=self._config.model,
            effort=self._config.effort,
            max_turns=self._config.max_turns,  # counted per reply, not per session (tried 2026-11-08)
            setting_sources=[],
            mcp_servers=mcp_servers,
            tools=BUILTIN_TOOLS,
            allowed_tools=[*BUILTIN_TOOLS, *(f"mcp__{name}" for name in mcp_servers)],
            permission_mode="dontAsk",
            env={
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                # With a claude.ai login, the account's connectors (Notion, Calendar,
                # Drive...) load automatically: ~200 tools, about 170k tokens on
                # every turn. The body needs none of them.
                "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            },
            resume=state.session_id,
            cli_path=shutil.which("claude"),
        )
        client = ClaudeSDKClient(options)
        await client.connect()
        self._client, self._state = client, state
        state.save()
        log.info("head connected (%s, effort %s, day %s, %s)", self._config.model, self._config.effort,
                 state.day, f"resumed {state.session_id}" if state.session_id else "new session")
        task = asyncio.create_task(self._report_tools(client))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _report_tools(self, client: ClaudeSDKClient) -> None:
        """Log which MCP servers the session really has, and how big its context is.

        On 2026-11-08 the body server had failed to start for ten hours and the
        context was 173k tokens, and neither showed anywhere; this makes both
        visible at every start.
        """
        try:
            deadline = time.monotonic() + MCP_STARTUP_WAIT_SECS
            while True:
                servers = (await client.get_mcp_status()).get("mcpServers", [])
                if time.monotonic() > deadline or all(s.get("status") != "pending" for s in servers):
                    break
                await asyncio.sleep(2)
            self.mcp_status = [
                {"name": s.get("name"), "status": s.get("status"),
                 "tools": len(s.get("tools", [])), "error": s.get("error")}
                for s in servers
            ]
            for server in self.mcp_status:
                if server["status"] != "connected":
                    log.error("head MCP server %s is %s: %s", server["name"], server["status"], server["error"])
            log.info("head MCP servers: %s", ", ".join(
                f"{s['name']} {s['status']} ({s['tools']} tools)" for s in self.mcp_status))

            usage = await client.get_context_usage()
            self.context_tokens = usage.get("totalTokens")
            parts = [f"{c['name']} {c['tokens']}" for c in usage.get("categories", [])
                     if c.get("kind") == "used" and c.get("tokens")]
            log.info("head context at start: %s tokens (%s)", self.context_tokens, ", ".join(parts))
            if self.context_tokens and self.context_tokens > CONTEXT_WARNING_TOKENS:
                log.warning("the head's context is unexpectedly large: %s tokens", self.context_tokens)
        except Exception:  # noqa: BLE001 - a report must not take the head down
            log.exception("could not read the head's MCP status")

    # ------------------------------------------------------------ turns

    async def reply(self, prompt: str) -> HeadReply:
        """One conversation turn. Turns are taken one at a time."""
        if self._client is None:
            raise RuntimeError("the head is still waking up")
        async with self._turn_lock:
            if self._state.day != self._today():  # in case the turnover task was late
                await self._close_the_day()
            self.is_thinking = True
            try:
                return await self._take_turn(prompt)
            finally:
                self.is_thinking = False

    async def _take_turn(self, prompt: str) -> HeadReply:
        await self._client.query(prompt)
        last_text = ""
        tokens_read = None
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                texts = [block.text for block in message.content if isinstance(block, TextBlock)]
                if texts:
                    last_text = "".join(texts)  # the text after the last tool call is the reply
            elif isinstance(message, ResultMessage):
                if message.session_id:
                    self._state.session_id = message.session_id
                tokens_read = _prompt_tokens(message.usage)
                if message.terminal_reason == "max_turns":
                    log.warning("the head used up max_turns (%d) in one reply", self._config.max_turns)
                elif message.is_error:
                    log.warning("the head ended the turn with an error: %s", message.result)
        self._state.turns += 1
        self._state.save()
        reply = parse_reply(last_text)
        reply.tokens_read = tokens_read
        return reply

    def _system_prompt(self) -> str:
        """SOUL.md, and - as PetitOnes' heads always read them - the reflection index and diary summary."""
        soul_path = REPO_ROOT / "SOUL.md"
        soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else "（SOUL.md はまだない）"
        return SYSTEM_PROMPT_TEMPLATE.format(
            companion=self._config.companion_name, day_starts_at=self._config.day_starts_at, soul=soul,
            reflection_index=_section("REFLECTION_INDEX.md（思索の索引。詳しくは SOUL_REFLECTIONS.md を Read）",
                                      REPO_ROOT / "REFLECTION_INDEX.md"),
            diary_summary=_section("diary_summary.md（最近の日記）", REPO_ROOT / "diary_summary.md"))

    def summary(self) -> dict:
        return {
            "ready": self.is_ready,
            "thinking": self.is_thinking,
            "session_id": self._state.session_id,
            "day": self._state.day,
            "replies": self._state.turns,
            "context_tokens": self.context_tokens,
            "mcp": self.mcp_status,
            "model": self._config.model,
        }


def _section(title: str, path: Path) -> str:
    if not path.exists():
        return ""
    return f"\n{title}\n{path.read_text(encoding='utf-8').strip()}\n"


def _prompt_tokens(usage: dict | None) -> int | None:
    """Input tokens of a reply, counting cache reads and writes (all of it was sent)."""
    if not usage:
        return None
    return sum(usage.get(key) or 0 for key in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
