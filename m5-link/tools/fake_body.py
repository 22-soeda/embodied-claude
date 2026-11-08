"""Pretend to be the M5 body, to test m5-link end to end without hardware.

Speaks a sentence (made with the same speech engine m5-link uses), sends it
the way the firmware does, and saves the spoken reply as reply.wav.

    uv run --project m5-link python m5-link/tools/fake_body.py \
        --url ws://127.0.0.1:8790/body --token TOKEN --say "こんにちは、聞こえる？"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from pathlib import Path

import aiohttp

AUDIO = 0x01
CHUNK_BYTES = 2048  # 1024 samples, like the firmware


async def synthesize(engine_url: str, text: str) -> bytes:
    async with aiohttp.ClientSession(base_url=engine_url) as session:
        async with session.get("/speakers") as response:
            speaker = (await response.json())[0]["styles"][0]["id"]
        async with session.post("/audio_query", params={"text": text, "speaker": speaker}) as response:
            query = await response.json()
        query.update(outputSamplingRate=16000, outputStereo=False)
        async with session.post("/synthesis", params={"speaker": speaker}, json=query) as response:
            wav_bytes = await response.read()
    path = Path("utterance.wav")
    path.write_bytes(wav_bytes)
    with wave.open(str(path)) as wav:
        return wav.readframes(wav.getnframes())


async def run(args: argparse.Namespace) -> None:
    utterance = await synthesize(args.engine, args.say)
    print(f"utterance: {len(utterance) / 32000:.1f} s")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{args.url}?token={args.token}") as socket:
            await socket.send_json({"t": "hello", "name": "fake-body", "fw": "test", "camera": False})
            await socket.send_json({"t": "state", "battery": 88, "charging": False, "near": True})

            started = time.monotonic()
            await socket.send_json({"t": "listen_start", "loop": False})
            for start in range(0, len(utterance), CHUNK_BYTES):
                await socket.send_bytes(bytes([AUDIO]) + utterance[start : start + CHUNK_BYTES])
                await asyncio.sleep(0.064)  # real time, as the microphone would
            await socket.send_json({"t": "listen_end", "reason": "silence"})
            spoke_at = time.monotonic()

            reply_pcm = bytearray()
            async for message in socket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(message.data)
                    print(f"+{time.monotonic() - spoke_at:5.1f}s  {data}")
                    if data.get("t") == "speak_end":
                        await socket.send_json({"t": "spoken"})
                        break
                elif message.type == aiohttp.WSMsgType.BINARY and message.data[0] == AUDIO:
                    reply_pcm.extend(message.data[1:])

    with wave.open("reply.wav", "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(reply_pcm))
    print(f"reply audio: {len(reply_pcm) / 32000:.1f} s -> reply.wav "
          f"(whole turn {time.monotonic() - started:.1f} s, after speaking {time.monotonic() - spoke_at:.1f} s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="ws://127.0.0.1:8790/body")
    parser.add_argument("--token", required=True)
    parser.add_argument("--engine", default="http://127.0.0.1:10101")
    parser.add_argument("--say", default="こんにちは、聞こえる？")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
