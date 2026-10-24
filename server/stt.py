"""Voice notes to text (owner: B). Real: ElevenLabs Scribe v2."""
import logging
import time
from typing import Protocol

import httpx

from server import settings

log = logging.getLogger("server.stt")


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


class FakeTranscriber:
    async def transcribe(self, audio: bytes) -> str:
        return "book a table for four tomorrow at eight at the usual Italian place"


class ScribeTranscriber:
    """ElevenLabs speech-to-text, model `scribe_v2` (checked against the API on 24 Oct 2026).
    The language is auto-detected, so German, English and Persian voice notes all work."""

    URL = "https://api.elevenlabs.io/v1/speech-to-text"

    async def transcribe(self, audio: bytes) -> str:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(self.URL, headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
                                  data={"model_id": "scribe_v2"}, files={"file": ("voice-note", audio)})
        if r.is_error:  # iMessage voice notes are accepted as is (tested live on 24 Oct 2026)
            raise RuntimeError(f"scribe failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        log.info("transcribed %d bytes (%s) in %.0f ms", len(audio), data.get("language_code"),
                 (time.monotonic() - start) * 1000)
        return data.get("text", "").strip()


transcriber: Transcriber = ScribeTranscriber() if settings.STT_MODE == "scribe" else FakeTranscriber()


async def transcribe(audio: bytes) -> str:
    return await transcriber.transcribe(audio)
