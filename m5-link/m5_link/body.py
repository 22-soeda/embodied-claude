"""The M5 body as the desktop sees it: one WebSocket, at most one body.

Messages are described in docs/m5-body-protocol.ja.md. This module only moves
them; what to do with an utterance or a picture is the Conversation's job,
reached through the callbacks set on Body.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web

log = logging.getLogger(__name__)

AUDIO = 0x01  # PCM 16-bit little endian, mono, 16 kHz
JPEG = 0x02
# Under the firmware WebSockets library's 15 KB frame limit (WEBSOCKETS_MAX_DATA_SIZE).
# The body takes in about one frame per loop, between face frames, so bigger
# frames carry speech faster: at 4 KB it came at about 60 KB/s (2026-11-09).
SPEECH_CHUNK_BYTES = 12288
SAMPLE_RATE = 16000


@dataclass
class BodyEvent:
    kind: str
    at: float = field(default_factory=time.time)
    detail: dict = field(default_factory=dict)


class Body:
    def __init__(self) -> None:
        self.on_utterance: Callable[[bytes, bool], Awaitable[None]] | None = None
        self.on_mitemite: Callable[[bytes], Awaitable[None]] | None = None
        self.on_event: Callable[[BodyEvent], Awaitable[None]] | None = None

        self.hello: dict = {}
        self.state: dict = {}
        self.events: deque[BodyEvent] = deque(maxlen=200)
        self.connected_at: float | None = None
        self.is_speaking = False

        self._socket: web.WebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._utterance = bytearray()
        self._is_listening = False
        self._listen_loop = False
        self._pending_jpeg: dict | None = None  # the snapshot header waiting for its bytes
        self._snapshot_waiters: dict[str, asyncio.Future[bytes]] = {}
        self._speech_done: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------ connection

    @property
    def is_connected(self) -> bool:
        return self._socket is not None and not self._socket.closed

    async def serve(self, request: web.Request) -> web.WebSocketResponse:
        """Run one body connection until it closes. A new body replaces the old."""
        # The Arduino WebSockets client always asks for the "arduino" subprotocol;
        # accepting it keeps aiohttp from warning on every connection.
        socket = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024, protocols=("arduino",))
        await socket.prepare(request)
        if self._socket is not None and not self._socket.closed:
            log.info("a new body connection replaces the old one")
            await self._socket.close()

        self._socket = socket
        self.connected_at = time.time()
        self._remember(BodyEvent("connected", detail={"peer": request.remote}))
        try:
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    await self._handle_text(message.json())
                elif message.type == WSMsgType.BINARY:
                    await self._handle_binary(message.data)
        finally:
            if self._socket is socket:
                self._socket = None
                self.connected_at = None
                self._is_listening = False
                self._remember(BodyEvent("disconnected"))
        return socket

    # ------------------------------------------------------------ to the body

    async def send(self, message: dict) -> bool:
        if not self.is_connected:
            return False
        async with self._send_lock:
            await self._socket.send_json(message)
        return True

    async def show_face(self, mood: str, duration_ms: int = 0) -> bool:
        return await self.send({"t": "face", "mood": mood, "ms": duration_ms})

    async def set_thinking(self, on: bool) -> bool:
        return await self.send({"t": "thinking", "on": on})

    async def speak(self, pcm: bytes, text: str, *, wait: bool = True) -> bool:
        """Send one utterance (16 kHz mono PCM) and, by default, wait until played."""
        if not self.is_connected or not pcm:
            return False
        self._speech_done.clear()
        self.is_speaking = True
        started = time.monotonic()
        async with self._send_lock:
            await self._socket.send_json(
                {"t": "speak_start", "bytes": len(pcm), "rate": SAMPLE_RATE, "text": text}
            )
            for start in range(0, len(pcm), SPEECH_CHUNK_BYTES):
                await self._socket.send_bytes(bytes([AUDIO]) + pcm[start : start + SPEECH_CHUNK_BYTES])
            await self._socket.send_json({"t": "speak_end"})
        playback_seconds = len(pcm) / 2 / SAMPLE_RATE
        if wait:
            try:
                await asyncio.wait_for(self._speech_done.wait(), timeout=playback_seconds + 10)
                # The caption shows at speak_start; the body reports "spoken" when
                # the sound ends. What is left over is how long the voice lagged
                # the caption (stops in the sound included, if any).
                took = time.monotonic() - started
                log.info("speech: %d KB (%.1f s of sound); the voice began about %.1f s after the caption",
                         len(pcm) // 1024, playback_seconds, took - playback_seconds)
            except TimeoutError:
                log.warning("the body did not report the end of speech")
        self.is_speaking = False
        return True

    async def take_snapshot(self, timeout: float = 10) -> bytes:
        """Ask the body for a picture; raises TimeoutError or ConnectionError."""
        if not self.is_connected:
            raise ConnectionError("the body is not connected")
        request_id = uuid.uuid4().hex[:8]
        waiter: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._snapshot_waiters[request_id] = waiter
        try:
            await self.send({"t": "snapshot", "id": request_id})
            return await asyncio.wait_for(waiter, timeout)
        finally:
            self._snapshot_waiters.pop(request_id, None)

    # ------------------------------------------------------------ from the body

    async def _handle_text(self, message: dict) -> None:
        kind = message.get("t")
        if kind == "hello":
            self.hello = message
            log.info("body says hello: %s", message)
        elif kind == "state":
            self.state = message
        elif kind == "event":
            detail = {key: value for key, value in message.items() if key not in ("t", "kind")}
            event = BodyEvent(message.get("kind", "?"), detail=detail)
            self._remember(event)
            if self.on_event:
                asyncio.create_task(self.on_event(event))
        elif kind == "listen_start":
            self._utterance.clear()
            self._is_listening = True
            self._listen_loop = bool(message.get("loop"))
        elif kind == "listen_end":
            await self._finish_utterance(message.get("reason", "?"))
        elif kind == "spoken" or kind == "speech_interrupted":
            self._speech_done.set()
        elif kind == "snapshot":
            self._pending_jpeg = message
        elif kind == "snapshot_failed":
            self._resolve_snapshot(message.get("id", ""), None)
        else:
            log.debug("unhandled message from the body: %s", message)

    async def _handle_binary(self, data: bytes) -> None:
        if not data:
            return
        kind, payload = data[0], data[1:]
        if kind == AUDIO and self._is_listening:
            self._utterance.extend(payload)
        elif kind == JPEG and self._pending_jpeg is not None:
            header, self._pending_jpeg = self._pending_jpeg, None
            if header.get("reason") == "mitemite":
                self._remember(BodyEvent("mitemite"))
                if self.on_mitemite:
                    asyncio.create_task(self.on_mitemite(payload))
            else:
                self._resolve_snapshot(header.get("id", ""), payload)

    async def _finish_utterance(self, reason: str) -> None:
        self._is_listening = False
        audio = bytes(self._utterance)
        self._utterance.clear()
        if reason in ("cancelled", "no_speech") or len(audio) < SAMPLE_RATE // 2:  # under 0.25 s
            return
        if self.on_utterance:
            asyncio.create_task(self.on_utterance(audio, self._listen_loop))

    def _resolve_snapshot(self, request_id: str, jpeg: bytes | None) -> None:
        waiter = self._snapshot_waiters.get(request_id)
        if waiter is None or waiter.done():
            return
        if jpeg is None:
            waiter.set_exception(RuntimeError("the body's camera failed"))
        else:
            waiter.set_result(jpeg)

    def _remember(self, event: BodyEvent) -> None:
        self.events.append(event)
        log.info("body event: %s", event.kind)

    def summary(self) -> dict:
        return {
            "connected": self.is_connected,
            "connected_at": self.connected_at,
            "hello": self.hello,
            "state": self.state,
            "speaking": self.is_speaking,
            "recent_events": [
                {"kind": e.kind, "at": e.at, **e.detail} for e in list(self.events)[-20:]
            ],
        }
