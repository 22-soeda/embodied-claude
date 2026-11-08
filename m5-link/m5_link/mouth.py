"""Text to speech through AivisSpeech Engine (VOICEVOX-compatible HTTP API).

The engine is asked for 16 kHz mono directly (outputSamplingRate), which is
what the body plays, so no resampling happens here. If the engine is not
running and engine_path is set, it is started in the background.

Words the engine reads wrong go into its user dictionary at start, from
[mouth.readings] in m5-link.toml. A name read wrong is the first thing a
companion notices; on 2026-11-08 添田 came out as ソエタ. The head can learn
to write the name in kana, but only while it remembers to.
"""

from __future__ import annotations

import asyncio
import io
import logging
import subprocess
import sys
import unicodedata
import wave
from pathlib import Path

import aiohttp

from .config import MouthConfig

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
ENGINE_START_TIMEOUT_S = 120


class Mouth:
    def __init__(self, config: MouthConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self.speaker_id: int | None = None
        self.speaker_name = ""

    @property
    def is_ready(self) -> bool:
        return self.speaker_id is not None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(base_url=self._config.url)
        if not await self._engine_answers():
            self._launch_engine()
            await self._wait_for_engine()
        await self._teach_readings()
        await self._choose_speaker()

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def synthesize(self, text: str) -> bytes:
        """Japanese text in, 16 kHz mono 16-bit PCM out."""
        if not self.is_ready:
            raise RuntimeError("the speech engine is not ready")
        params = {"text": text, "speaker": self.speaker_id}
        async with self._session.post("/audio_query", params=params) as response:
            response.raise_for_status()
            query = await response.json()
        query.update(outputSamplingRate=SAMPLE_RATE, outputStereo=False, speedScale=self._config.speed)
        async with self._session.post("/synthesis", params={"speaker": self.speaker_id}, json=query) as response:
            response.raise_for_status()
            wav_bytes = await response.read()
        return _pcm_from_wav(wav_bytes)

    # ------------------------------------------------------------ engine

    async def _engine_answers(self) -> bool:
        try:
            async with self._session.get("/version", timeout=aiohttp.ClientTimeout(total=3)) as response:
                return response.status == 200
        except aiohttp.ClientError:
            return False
        except TimeoutError:
            return False

    def _launch_engine(self) -> None:
        engine = Path(self._config.engine_path).expanduser() if self._config.engine_path else None
        if engine is None or not engine.exists():
            raise RuntimeError(f"the speech engine is not running and was not found at {engine}")
        port = self._config.url.rsplit(":", 1)[-1].strip("/")
        log.info("starting the speech engine: %s", engine)
        subprocess.Popen(
            [str(engine), "--host", "127.0.0.1", "--port", port],
            cwd=engine.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

    async def _wait_for_engine(self) -> None:
        for _ in range(ENGINE_START_TIMEOUT_S):
            if await self._engine_answers():
                return
            await asyncio.sleep(1)
        raise RuntimeError("the speech engine did not answer in time")

    async def _teach_readings(self) -> None:
        """Make the engine's user dictionary agree with [mouth.readings]: add or correct each word."""
        if not self._config.readings:
            return
        async with self._session.get("/user_dict") as response:
            response.raise_for_status()
            known_words = await response.json()  # word uuid -> word
        uuid_by_surface = {_same_width(word["surface"]): (uuid, word) for uuid, word in known_words.items()}

        for surface, reading in self._config.readings.items():
            pronunciation, accent_type = parse_reading(reading)
            params = {"surface": surface, "pronunciation": pronunciation,
                      "accent_type": accent_type, "word_type": "PROPER_NOUN"}
            uuid, word = uuid_by_surface.get(_same_width(surface), (None, None))
            if word and word.get("pronunciation") == pronunciation and word.get("accent_type") == accent_type:
                continue
            if uuid:
                request = self._session.put(f"/user_dict_word/{uuid}", params=params)
            else:
                request = self._session.post("/user_dict_word", params=params)
            async with request as response:
                if response.status >= 400:
                    log.warning("the speech engine did not take the reading %s -> %s: %s",
                                surface, pronunciation, await response.text())
                    continue
            log.info("taught the speech engine to read %s as %s", surface, pronunciation)

    async def _choose_speaker(self) -> None:
        async with self._session.get("/speakers") as response:
            response.raise_for_status()
            speakers = await response.json()
        styles = [(speaker["name"], style) for speaker in speakers for style in speaker["styles"]]
        if not styles:
            raise RuntimeError("the speech engine has no voices installed")

        wanted = self._config.speaker
        chosen = next(((name, style) for name, style in styles if style["id"] == wanted), styles[0])
        self.speaker_id = chosen[1]["id"]
        self.speaker_name = f"{chosen[0]} ({chosen[1]['name']})"
        log.info("voice: %s, style id %s", self.speaker_name, self.speaker_id)


def parse_reading(reading: str | list) -> tuple[str, int]:
    """'ソエダ' -> ('ソエダ', 0): flat accent unless given, as in ['ハシ', 1].

    The accent is where the pitch falls, counted in morae; 0 means it never
    falls, which suits most family names (マエダ, ヨシダ).
    """
    if isinstance(reading, str):
        return reading, 0
    pronunciation, accent_type = reading
    return str(pronunciation), int(accent_type)


def _same_width(text: str) -> str:
    """The engine stores surfaces in full width; compare both sides the same way."""
    return unicodedata.normalize("NFKC", text)


def _pcm_from_wav(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        if wav.getframerate() != SAMPLE_RATE or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise RuntimeError(
                f"unexpected audio from the engine: {wav.getframerate()} Hz, "
                f"{wav.getnchannels()} ch, {wav.getsampwidth() * 8} bit"
            )
        return wav.readframes(wav.getnframes())
