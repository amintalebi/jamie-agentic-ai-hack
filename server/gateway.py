"""iMessage gateway (owner: B). Sending, inbound routing, Linq webhook.

user_id is the user's E.164 phone number, so `to` and `user_id` are the same string.
"""
import logging
from typing import Protocol

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from common.masking import mask_phone
from server import calls, orchestrator, settings, stt

log = logging.getLogger("server.gateway")
router = APIRouter()


class InboundMessage(BaseModel):
    message_id: str
    user_id: str                       # sender, E.164
    text: str = ""
    audio_url: str | None = None       # voice note attachment
    vote: str | None = None            # chosen poll option, if this is a poll vote


class Messenger(Protocol):
    async def send_text(self, to: str, text: str) -> None: ...
    async def send_poll(self, to: str, question: str, options: list[str]) -> None: ...
    async def set_typing(self, to: str, on: bool) -> None: ...
    async def download_attachment(self, url: str) -> bytes: ...


class FakeMessenger:
    """Logs outgoing messages. Drive inbound traffic with POST /dev/inbound."""

    def __init__(self) -> None:
        self.sent: list[dict] = []  # inspect in tests

    async def send_text(self, to: str, text: str) -> None:
        self.sent.append({"to": to, "text": text})
        log.info("[fake send_text] to=%s %r", mask_phone(to), text)

    async def send_poll(self, to: str, question: str, options: list[str]) -> None:
        self.sent.append({"to": to, "question": question, "options": options})
        log.info("[fake send_poll] to=%s %r %s", mask_phone(to), question, options)

    async def set_typing(self, to: str, on: bool) -> None:
        pass

    async def download_attachment(self, url: str) -> bytes:
        return b""


class LinqMessenger:
    """TODO(B): Linq API v3, check https://docs.linqapp.com for endpoint shapes.
    send_poll falls back to numbered text options if polls fail."""

    async def send_text(self, to: str, text: str) -> None:
        raise NotImplementedError

    async def send_poll(self, to: str, question: str, options: list[str]) -> None:
        raise NotImplementedError

    async def set_typing(self, to: str, on: bool) -> None:
        raise NotImplementedError

    async def download_attachment(self, url: str) -> bytes:
        raise NotImplementedError


messenger: Messenger = LinqMessenger() if settings.MESSENGER_MODE == "linq" else FakeMessenger()


async def send_text(to: str, text: str) -> None:
    await messenger.send_text(to, text)


async def send_poll(to: str, question: str, options: list[str]) -> None:
    await messenger.send_poll(to, question, options)


_seen_ids: set[str] = set()


async def route_inbound(msg: InboundMessage) -> None:
    """Dedupe, transcribe voice notes, then send votes to the pending call question or the orchestrator."""
    if msg.message_id in _seen_ids:
        return
    _seen_ids.add(msg.message_id)

    if msg.vote is not None:
        call_id = calls.pending_question(msg.user_id)
        if call_id:
            await calls.resolve_answer(call_id, msg.vote)
            return
        text = msg.vote  # vote on an orchestrator poll: treat the option as the user's reply
    elif msg.audio_url:
        audio = await messenger.download_attachment(msg.audio_url)
        text = await stt.transcribe(audio)
    else:
        text = msg.text

    await messenger.set_typing(msg.user_id, True)
    try:
        await orchestrator.handle_message(msg.user_id, text)
    finally:
        await messenger.set_typing(msg.user_id, False)


@router.post("/webhooks/linq")
async def linq_webhook(request: Request, background: BackgroundTasks) -> dict:
    # TODO(B): verify signature with LINQ_WEBHOOK_SECRET, parse into InboundMessage, then
    # background.add_task(route_inbound, msg). Return 200 fast.
    log.info("linq webhook received (not parsed yet)")
    return {"ok": True}


dev_router = APIRouter(prefix="/dev")


@dev_router.post("/inbound")
async def dev_inbound(msg: InboundMessage, background: BackgroundTasks) -> dict:
    """Fake an inbound iMessage or poll vote (only mounted when MESSENGER_MODE=fake)."""
    background.add_task(route_inbound, msg)
    return {"ok": True}
