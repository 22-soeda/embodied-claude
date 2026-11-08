"""Speech recognition with faster-whisper, on the GPU when it can be used.

On Windows, CTranslate2 needs cuBLAS and cuDNN DLLs it can find. The `cuda`
extra installs them as pip packages; their bin folders are added to the DLL
search path here. If the GPU still fails to load, the CPU is used instead,
with a smaller model so a short utterance stays around a second or two.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import numpy as np

from .config import EarsConfig

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
GPU_MODEL = "large-v3-turbo"
CPU_MODEL = "small"


def _add_nvidia_dll_directories() -> None:
    if sys.platform != "win32":
        return
    try:
        import nvidia  # namespace package from nvidia-cublas-cu12 / nvidia-cudnn-cu12
    except ImportError:
        return
    for package_root in nvidia.__path__:
        for bin_dir in Path(package_root).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


class Ears:
    def __init__(self, config: EarsConfig) -> None:
        self._config = config
        self._model = None
        self.device = "not loaded"
        self.model_name = ""

    async def load(self) -> None:
        await asyncio.to_thread(self._load_blocking)

    def _load_blocking(self) -> None:
        from faster_whisper import WhisperModel

        _add_nvidia_dll_directories()
        attempts = []
        if self._config.device in ("auto", "cuda"):
            attempts.append(("cuda", "float16", self._model_name_for("cuda")))
        if self._config.device in ("auto", "cpu"):
            attempts.append(("cpu", "int8", self._model_name_for("cpu")))

        for device, compute_type, model_name in attempts:
            try:
                model = WhisperModel(model_name, device=device, compute_type=compute_type)
                # A real decode, because a broken cuDNN only fails at this point.
                list(model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language=self._config.language)[0])
            except Exception as error:  # noqa: BLE001 - any failure means "try the next device"
                log.warning("speech recognition on %s failed to start: %s", device, error)
                continue
            self._model, self.device, self.model_name = model, device, model_name
            log.info("speech recognition ready: %s on %s", model_name, device)
            return
        raise RuntimeError("speech recognition could not start on any device")

    def _model_name_for(self, device: str) -> str:
        if self._config.model != "auto":
            return self._config.model
        return GPU_MODEL if device == "cuda" else CPU_MODEL

    async def transcribe(self, pcm: bytes) -> str:
        """16 kHz mono 16-bit PCM in, text out ("" when nothing was said)."""
        if self._model is None:
            raise RuntimeError("speech recognition is not loaded yet")
        return await asyncio.to_thread(self._transcribe_blocking, pcm)

    def _transcribe_blocking(self, pcm: bytes) -> str:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            samples,
            language=self._config.language,
            beam_size=1,
            vad_filter=True,  # the body's end-of-speech guess leaves silence at both ends
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments).strip()
