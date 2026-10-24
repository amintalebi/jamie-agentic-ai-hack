"""iMessage gateway (owner: B). Sending, inbound routing, Linq webhook.

user_id is the user's E.164 phone number, so `to` and `user_id` are the same string.
"""
import logging
import uuid
from typing import Protocol

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from common.masking import mask_phone, mask_text
from server import calls, orchestrator, settings, stt

log = logging.getLogger("server.gateway")
router = APIRouter()


class InboundMessage(BaseModel):
    message_id: str
    user_id: str                       # sender, E.164
    text: str = ""
    audio_url: str | None = None       # voice note attachment
    vote: str | None = None            # chosen poll option, if this is a poll vote
    poll_id: str | None = None         # id of the poll the vote belongs to (what send_poll returned)


class Messenger(Protocol):
    async def send_text(self, to: str, text: str) -> None: ...
    async def send_poll(self, to: str, question: str, options: list[str]) -> str | None:
        """Returns an id that later votes on this poll carry as InboundMessage.poll_id (None if unknown)."""
        ...

    async def set_typing(self, to: str, on: bool) -> None: ...
    async def download_attachment(self, url: str) -> bytes: ...


class FakeMessenger:
    """Logs outgoing messages. Drive inbound traffic with POST /dev/inbound."""

    def __init__(self) -> None:
        self.sent: list[dict] = []  # inspect in tests

    async def send_text(self, to: str, text: str) -> None:
        self.sent.append({"to": to, "text": text})
        log.info("[fake send_text] to=%s %r", mask_phone(to), mask_text(text))

    async def send_poll(self, to: str, question: str, options: list[str]) -> str | None:
        poll_id = uuid.uuid4().hex[:8]
        self.sent.append({"to": to, "question": question, "options": options, "poll_id": poll_id})
        log.info("[fake send_poll] to=%s poll=%s %r %s", mask_phone(to), poll_id, mask_text(question), options)
        return poll_id

    async def set_typing(self, to: str, on: bool) -> None:
        pass

    async def download_attachment(self, url: str) -> bytes:
        return b""


class LinqMessenger:
    """TODO(B): Linq API v3, check https://docs.linqapp.com for endpoint shapes.
    send_poll falls back to numbered text options if polls fail."""

    async def send_text(self, to: str, text: str) -> None:
        raise NotImplementedError

    async def send_poll(self, to: str, question: str, options: list[str]) -> str | None:
        # TODO(B): return the Linq poll/message id that vote webhooks reference.
        raise NotImplementedError

    async def set_typing(self, to: str, on: bool) -> None:
        raise NotImplementedError

    async def download_attachment(self, url: str) -> bytes:
        raise NotImplementedError


messenger: Messenger = LinqMessenger() if settings.MESSENGER_MODE == "linq" else FakeMessenger()


async def send_text(to: str, text: str) -> None:
    await messenger.send_text(to, text)


async def send_poll(to: str, question: str, options: list[str]) -> str | None:
    """Send a poll; returns its id so votes can be matched to it."""
    return await messenger.send_poll(to, question, options)


_seen_ids: set[str] = set()


async def route_inbound(msg: InboundMessage) -> None:
    """Dedupe, transcribe voice notes, then send votes to the matching call question or the orchestrator.
    Never fails silently: on any error the user gets a short apology text."""
    if msg.message_id in _seen_ids:
        return
    _seen_ids.add(msg.message_id)  # stays marked on failure: the user is told, a retry would double-reply
    try:
        if msg.vote is not None:
            call_id = calls.match_vote(msg.user_id, msg.vote, msg.poll_id)
            if call_id:
                await calls.resolve_answer(call_id, msg.vote)
                return
            text = msg.vote  # vote on an orchestrator poll: treat the option as the user's reply
        elif msg.audio_url:
            audio = await messenger.download_attachment(msg.audio_url)
            text = await stt.transcribe(audio)
        else:
            text = msg.text

        await _typing(msg.user_id, True)
        try:
            await orchestrator.handle_message(msg.user_id, text)
        finally:
            await _typing(msg.user_id, False)
    except Exception:
        log.exception("handling message %s from %s failed", msg.message_id, mask_phone(msg.user_id))
        try:
            await send_text(msg.user_id, "Sorry, something went wrong on my side. Can you try again?")
        except Exception:
            log.exception("could not send the error text to %s", mask_phone(msg.user_id))


async def _typing(to: str, on: bool) -> None:
    """Best effort: a broken typing indicator must not break the turn."""
    try:
        await messenger.set_typing(to, on)
    except Exception as e:
        log.warning("typing indicator failed: %s", e)


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
