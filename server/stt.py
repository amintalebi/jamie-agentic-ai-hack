"""Voice notes to text (owner: B). Real: ElevenLabs Scribe v2."""
import logging
from typing import Protocol

from server import settings

log = logging.getLogger("server.stt")


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


class FakeTranscriber:
    async def transcribe(self, audio: bytes) -> str:
        return "book a table for four tomorrow at eight at the usual Italian place"


class ScribeTranscriber:
    """TODO(B): ElevenLabs speech-to-text, Scribe v2. Verify the model id in ElevenLabs docs."""

    async def transcribe(self, audio: bytes) -> str:
        raise NotImplementedError


transcriber: Transcriber = ScribeTranscriber() if settings.STT_MODE == "scribe" else FakeTranscriber()


async def transcribe(audio: bytes) -> str:
    return await transcriber.transcribe(audio)
