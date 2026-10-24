"""Per-user voice clones (owner: C): upload a sample, clone it at ElevenLabs, remember the id.

The id is stored as the user fact `voice_id`, so it rides into every CallBrief through
`user_facts` with no contract change. The call agent prefers it over the env default.

Routes are mounted under /api/voices and require a logged-in dashboard session.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from common.masking import mask_phone
from server import auth, memory, settings

log = logging.getLogger("server.voices")
router = APIRouter(prefix="/api/voices")

API = "https://api.elevenlabs.io/v1"
TIMEOUT_S = 60.0
MAX_BYTES = 10 * 1024 * 1024        # ElevenLabs takes more, but keep a phone upload sane
MIN_BYTES = 8 * 1024                # a sample this small is never usable
ALLOWED_TYPES = ("audio/", "video/mp4", "application/octet-stream")

VOICE_FACT = "voice_id"
VOICE_NAME_FACT = "voice_name"


def _headers() -> dict[str, str]:
    if not settings.ELEVENLABS_API_KEY:
        raise HTTPException(503, "voice cloning is not configured on this server")
    return {"xi-api-key": settings.ELEVENLABS_API_KEY}


async def _delete_remote(voice_id: str) -> None:
    """Free the clone slot for a replaced voice. Never fatal: the new voice already works."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.delete(f"{API}/voices/{voice_id}", headers=_headers())
        if r.is_error:
            log.warning("could not delete old voice %s: %s %s", voice_id, r.status_code, r.text[:120])
    except Exception:
        log.exception("deleting old voice %s failed", voice_id)


@router.get("")
async def get_voice(user_id: str = Depends(auth.current_user)) -> dict:
    """The voice this user's calls currently use, if they cloned one."""
    facts = await memory.get_facts(user_id)
    voice_id = facts.get(VOICE_FACT) or None
    return {
        "voice_id": voice_id,
        "voice_name": (facts.get(VOICE_NAME_FACT) or None) if voice_id else None,
        "configured": bool(settings.ELEVENLABS_API_KEY),
    }


@router.post("")
async def create_voice(sample: UploadFile, user_id: str = Depends(auth.current_user)) -> dict:
    """Clone the uploaded sample and use it for this user's calls from now on."""
    if sample.content_type and not sample.content_type.startswith(ALLOWED_TYPES):
        raise HTTPException(415, f"unsupported audio type {sample.content_type}")
    data = await sample.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "sample is larger than 10 MB, please record something shorter")
    if len(data) < MIN_BYTES:
        raise HTTPException(422, "sample is too short, record about 30 seconds of speech")

    facts = await memory.get_facts(user_id)
    # One clone per user, named after them so the ElevenLabs dashboard stays readable.
    name = f"jamie-{facts.get('name', 'user')}-{user_id[-4:]}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(
                f"{API}/voices/add",
                headers=_headers(),
                data={"name": name, "remove_background_noise": "true"},
                files={"files": (sample.filename or "sample.m4a", data,
                                 sample.content_type or "audio/mpeg")},
            )
    except httpx.HTTPError as exc:
        log.exception("voice clone upload failed for %s", mask_phone(user_id))
        raise HTTPException(502, "could not reach ElevenLabs, try again") from exc

    if r.is_error:
        log.error("voice clone rejected for %s: %s %s", mask_phone(user_id), r.status_code, r.text[:200])
        detail = "ElevenLabs rejected the sample"
        if r.status_code in (401, 403):
            detail = "the server's ElevenLabs key cannot create voices"
        elif r.status_code == 422:
            detail = "the sample was not usable, record about 30 seconds of clear speech"
        raise HTTPException(502, detail)

    voice_id = (r.json() or {}).get("voice_id")
    if not voice_id:
        raise HTTPException(502, "ElevenLabs did not return a voice id")

    previous = facts.get(VOICE_FACT)
    await memory.remember(user_id, VOICE_FACT, voice_id)
    await memory.remember(user_id, VOICE_NAME_FACT, name)
    log.info("cloned voice for %s: %s (%d bytes)", mask_phone(user_id), voice_id, len(data))
    if previous and previous != voice_id:
        await _delete_remote(previous)
    return {"voice_id": voice_id, "voice_name": name, "configured": True}


@router.delete("")
async def delete_voice(user_id: str = Depends(auth.current_user)) -> dict:
    """Stop using a cloned voice and free the slot. Calls fall back to the server default."""
    facts = await memory.get_facts(user_id)
    voice_id = facts.get(VOICE_FACT)
    if not voice_id:
        return {"voice_id": None}
    await memory.remember(user_id, VOICE_FACT, "")
    await memory.remember(user_id, VOICE_NAME_FACT, "")
    await _delete_remote(voice_id)
    log.info("removed cloned voice for %s", mask_phone(user_id))
    return {"voice_id": None}
