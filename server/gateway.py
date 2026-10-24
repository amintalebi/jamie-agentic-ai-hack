"""iMessage gateway (owner: B). Sending, inbound routing, Linq webhook.

user_id is the user's E.164 phone number, so `to` and `user_id` are the same string.
"""
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Protocol

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
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


LINQ_BASE_URL = "https://api.linqapp.com/api/partner/v3"


class LinqError(Exception):
    pass


class LinqMessenger:
    """Linq Partner API v3 (spec: https://cdn.linqapp.com/openapi/linq-api-v3.yaml).

    Texts go out via POST /v3/messages, which picks our line and reuses the chat with this user.
    Linq polls have no question field, so the question is sent as a text first. Votes arrive as
    option ids, so each poll's options are kept here to turn the id back into the option text.
    """

    def __init__(self) -> None:
        self._api = httpx.AsyncClient(base_url=LINQ_BASE_URL, timeout=15,
                                      headers={"Authorization": f"Bearer {settings.LINQ_API_KEY}"})
        self._files = httpx.AsyncClient(timeout=30, follow_redirects=True)  # presigned URLs, no auth header
        self._chat_ids: dict[str, str] = {}                 # user handle -> Linq chat id
        self._poll_options: dict[str, dict[str, str]] = {}  # poll message id -> {option_id: text}
        self._numbered: dict[str, list[str]] = {}           # user -> options of a numbered-text fallback

    async def _request(self, method: str, path: str, **kw) -> dict:
        start = time.monotonic()
        try:
            r = await self._api.request(method, path, **kw)
        except httpx.HTTPError as e:
            log.warning("linq %s %s failed: %s", method, path, e)
            raise LinqError(f"{method} {path}: {e}") from e
        log.info("linq %s %s -> %s (%.0f ms)", method, path, r.status_code, (time.monotonic() - start) * 1000)
        if r.is_error:
            raise LinqError(f"{method} {path}: {r.status_code} {mask_text(r.text[:300])}")
        return r.json() if r.content else {}

    def remember_chat(self, user: str, chat_id: str) -> None:
        self._chat_ids[user] = chat_id

    async def send_text(self, to: str, text: str) -> None:
        body = {"to": [to], "message": {"parts": [{"type": "text", "value": text}]}}
        data = await self._request("POST", "/messages", json=body)
        self._chat_ids[to] = data["chat_id"]
        log.info("sent text to %s: %r", mask_phone(to), mask_text(text))

    async def send_poll(self, to: str, question: str, options: list[str]) -> str | None:
        await self.send_text(to, question)  # also guarantees the chat exists: a poll can't start a chat
        try:
            data = await self._request("POST", f"/chats/{self._chat_ids[to]}/polls",
                                       json={"poll": {"options": [{"text": o} for o in options]}})
        except LinqError as e:
            log.warning("poll failed, sending numbered options instead: %s", e)
            self._numbered[to] = list(options)
            lines = [f"{i}. {o}" for i, o in enumerate(options, 1)]
            await self.send_text(to, "\n".join(lines) + "\nReply with the number.")
            return None
        poll_id = data["message_id"]
        self._poll_options[poll_id] = {o["option_id"]: o["text"] for o in data["poll"]["options"]}
        self._numbered.pop(to, None)
        return poll_id

    async def option_text(self, poll_id: str, option_id: str) -> str | None:
        """The text of a voted option. Polls sent before a restart are looked up on Linq."""
        if option_id not in self._poll_options.get(poll_id, {}):
            data = await self._request("GET", f"/messages/{poll_id}/poll")
            options = (data.get("poll") or data).get("options") or []
            self._poll_options[poll_id] = {o["option_id"]: o["text"] for o in options}
        return self._poll_options[poll_id].get(option_id)

    def numbered_choice(self, user: str, text: str) -> str | None:
        """Turn a "2" reply to a numbered-text fallback poll into that option."""
        options = self._numbered.get(user)
        if not options or not text.strip().isdigit():
            return None
        n = int(text.strip())
        if not 1 <= n <= len(options):
            return None
        del self._numbered[user]
        return options[n - 1]

    async def set_typing(self, to: str, on: bool) -> None:
        chat_id = self._chat_ids.get(to)
        if chat_id is None:  # no chat yet; the first reply creates it
            return
        await self._request("POST" if on else "DELETE", f"/chats/{chat_id}/typing")

    async def download_attachment(self, url: str) -> bytes:
        start = time.monotonic()
        r = await self._files.get(url)
        r.raise_for_status()
        log.info("downloaded attachment, %d bytes (%.0f ms)", len(r.content), (time.monotonic() - start) * 1000)
        return r.content


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

        # Typing starts before the voice-note download and transcription (~1.2 s), not after.
        await _typing(msg.user_id, True)
        try:
            if msg.vote is not None:
                text = msg.vote  # vote on an orchestrator poll: treat the option as the user's reply
            elif msg.audio_url:
                audio = await messenger.download_attachment(msg.audio_url)
                text = await stt.transcribe(audio)
            else:
                text = msg.text
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


WEBHOOK_TOLERANCE_S = 300


def verify_linq_signature(body: bytes, headers, secret: str, now: float | None = None) -> bool:
    """Standard Webhooks: base64 HMAC-SHA256 of "{webhook-id}.{webhook-timestamp}.{body}",
    keyed with the base64 part of the whsec_ secret, sent as "v1,<sig>" (space-separated list)."""
    msg_id, ts, sigs = (headers.get(h) for h in ("webhook-id", "webhook-timestamp", "webhook-signature"))
    if not (msg_id and ts and sigs):
        return False
    try:
        if abs((now or time.time()) - int(ts)) > WEBHOOK_TOLERANCE_S:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, s.removeprefix("v1,")) for s in sigs.split() if s.startswith("v1,"))


async def parse_linq_event(event: dict) -> InboundMessage | None:
    """Turn a Linq webhook event (version 2026-02-03) into an InboundMessage, or None to ignore it."""
    etype, data = event.get("event_type"), event.get("data") or {}
    sender = (data.get("sender_handle") or {}).get("handle")
    chat = data.get("chat") or {}
    if data.get("direction") != "inbound" or not sender:
        return None
    if chat.get("is_group"):  # TODO(hack): 1:1 chats only
        log.info("ignoring %s from a group chat", etype)
        return None
    linq = messenger if isinstance(messenger, LinqMessenger) else None
    if linq and chat.get("id"):
        linq.remember_chat(sender, chat["id"])

    if etype == "message.received":
        parts = data.get("parts") or []
        text = " ".join(p.get("value", "") for p in parts if p.get("type") == "text").strip()
        audio = next((p["url"] for p in parts
                      if p.get("type") == "media" and str(p.get("mime_type", "")).startswith("audio/")), None)
        choice = linq.numbered_choice(sender, text) if linq and not audio else None
        if choice:
            return InboundMessage(message_id=data["id"], user_id=sender, vote=choice)
        if not text and not audio:  # TODO(hack): photos, stickers, link cards
            log.info("ignoring message %s with no text or audio", data.get("id"))
            return None
        return InboundMessage(message_id=data["id"], user_id=sender, text=text, audio_url=audio)

    if etype == "poll.vote.added":
        option = await linq.option_text(data["message_id"], data["option_id"]) if linq else None
        if option is None:
            log.warning("vote for unknown option %s on poll %s", data.get("option_id"), data.get("message_id"))
            return None
        # A vote can be toggled off and on again, so each event is a new message.
        return InboundMessage(message_id=f"vote:{event.get('event_id')}", user_id=sender,
                              vote=option, poll_id=data["message_id"])

    # TODO(hack): tapbacks (reaction.added) as yes/no answers to a pending question.
    log.info("linq event %s ignored", etype)
    return None


async def _handle_linq_event(event: dict) -> None:
    try:
        msg = await parse_linq_event(event)
    except Exception:
        log.exception("could not parse linq event %s (%s)", event.get("event_id"), event.get("event_type"))
        return
    if msg is not None:
        await route_inbound(msg)


@router.post("/webhooks/linq")
async def linq_webhook(request: Request, background: BackgroundTasks) -> dict:
    """Verify, return 200 fast, handle in the background (Linq times out after 10 s and retries)."""
    body = await request.body()
    if settings.LINQ_WEBHOOK_SECRET:
        if not verify_linq_signature(body, request.headers, settings.LINQ_WEBHOOK_SECRET):
            log.warning("linq webhook with a bad signature rejected")
            raise HTTPException(status_code=401, detail="bad signature")
    else:
        log.warning("LINQ_WEBHOOK_SECRET not set, webhook signature not checked")  # TODO(hack)
    try:
        event = json.loads(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid JSON") from e
    log.info("linq webhook %s %s", event.get("event_type"), event.get("event_id"))
    background.add_task(_handle_linq_event, event)
    return {"ok": True}


dev_router = APIRouter(prefix="/dev")


@dev_router.post("/inbound")
async def dev_inbound(msg: InboundMessage, background: BackgroundTasks) -> dict:
    """Fake an inbound iMessage or poll vote (only mounted when MESSENGER_MODE=fake)."""
    background.add_task(route_inbound, msg)
    return {"ok": True}
