"""HTTP client that posts CallEvents to the server and long-polls for the user's answer."""
import asyncio
import logging
import os
from collections import deque

import httpx

from contracts_local import CallEvent  # TODO(hack): switch to server.contracts
from common.masking import mask_phone, mask_text  # noqa: F401  (contracts_local puts the repo root on sys.path)

log = logging.getLogger("call_agent.events")

PARTIAL_INTERVAL_S = 0.3
FLUSH_TIMEOUT_S = 2.0


class EventsClient:
    def __init__(self, server_url: str, call_id: str, timeout: float = 5.0):
        self.server_url = server_url.rstrip("/")
        self.call_id = call_id
        self.timeout = timeout
        # The server rejects these routes without CALL_EVENTS_SECRET once it has one set.
        secret = os.getenv("CALL_EVENTS_SECRET", "").strip()
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)
        # Live transcript (POST /calls/{id}/transcript): one sender task keeps lines in order.
        self._finals: deque[dict] = deque()      # finished lines, sent in order before any partial
        self._partial: dict | None = None        # newest "still talking" text, only the latest is sent
        self._wake = asyncio.Event()
        self._sender: asyncio.Task | None = None
        self._line_failures = 0
        self._sending_final = False             # a finished line is on the wire right now

    def post_line(self, speaker: str, text: str, final: bool = True) -> None:
        """Queue a live transcript line for the dashboard. Never blocks or raises; the call goes on."""
        text = (text or "").strip()
        if not text:
            return
        line = {"speaker": speaker, "text": text, "final": final}
        if final:
            self._finals.append(line)
            if self._partial and self._partial["speaker"] == speaker:
                self._partial = None  # superseded: never send a stale partial after its final line
        else:
            self._partial = line
        self._wake.set()
        if self._sender is None:
            self._sender = asyncio.create_task(self._send_lines())

    async def _send_lines(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._finals or self._partial:
                if self._finals:
                    self._sending_final = True
                    try:
                        await self._post_line(self._finals.popleft())
                    finally:
                        self._sending_final = False
                else:
                    line, self._partial = self._partial, None
                    await self._post_line(line)
                    # At most ~3 partials a second; whatever arrives meanwhile replaces the slot,
                    # so the newest text still goes out right after the pause.
                    await asyncio.sleep(PARTIAL_INTERVAL_S)

    async def _post_line(self, line: dict) -> None:
        url = f"{self.server_url}/calls/{self.call_id}/transcript"
        try:
            r = await self._client.post(url, json=line, timeout=3.0)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001  the call must go on whatever happens here
            self._line_failures += 1
            if self._line_failures in (1, 10, 100):  # visible, but no log flood
                log.warning("live transcript line FAILED call=%s (%d so far): %s",
                            self.call_id, self._line_failures, mask_text(str(e)))

    async def post(self, event: CallEvent) -> None:
        """POST {server_url}/calls/{call_id}/events. Logs and swallows errors so the call keeps going."""
        url = f"{self.server_url}/calls/{self.call_id}/events"
        try:
            r = await self._client.post(url, json=event.model_dump(mode="json"))
            r.raise_for_status()
            log.info("event %s posted call=%s text=%r", event.type, self.call_id, mask_text(event.text)[:120])
        except httpx.HTTPError as e:
            log.error("event %s FAILED call=%s: %s", event.type, self.call_id, mask_text(str(e)))

    async def wait_answer(self, wait: int = 40) -> str | None:
        """GET {server_url}/calls/{call_id}/answer?wait=N. Returns the answer, or None on timeout/error."""
        url = f"{self.server_url}/calls/{self.call_id}/answer"
        try:
            # The server holds the request up to `wait` s, so the read timeout must be longer.
            r = await self._client.get(url, params={"wait": wait}, timeout=wait + self.timeout)
            r.raise_for_status()
            answer = r.json().get("answer")
            log.info("answer call=%s -> %r", self.call_id, mask_text(answer) if answer else None)
            return answer
        except (httpx.HTTPError, ValueError) as e:
            log.error("wait_answer FAILED call=%s: %s", self.call_id, mask_text(str(e)))
            return None

    async def aclose(self) -> None:
        """At hangup: give queued transcript lines a moment to go out, then stop and close."""
        if self._sender is not None:
            self._partial = None
            loop = asyncio.get_running_loop()
            deadline = loop.time() + FLUSH_TIMEOUT_S
            while (self._finals or self._sending_final) and loop.time() < deadline:
                await asyncio.sleep(0.05)
            self._sender.cancel()
            try:
                await self._sender
            except asyncio.CancelledError:
                pass
            if self._finals:
                log.warning("live transcript: %d lines not sent at hangup call=%s", len(self._finals), self.call_id)
        if self._line_failures:
            log.warning("live transcript: %d lines failed in total call=%s", self._line_failures, self.call_id)
        await self._client.aclose()
