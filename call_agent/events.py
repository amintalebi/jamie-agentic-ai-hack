"""HTTP client that posts CallEvents to the server and long-polls for the user's answer."""
import asyncio
import logging
import os
import time

import httpx

from contracts_local import CallEvent  # TODO(hack): switch to server.contracts
from common.masking import mask_phone, mask_text  # noqa: F401  (contracts_local puts the repo root on sys.path)

log = logging.getLogger("call_agent.events")


class EventsClient:
    def __init__(self, server_url: str, call_id: str, timeout: float = 5.0):
        self.server_url = server_url.rstrip("/")
        self.call_id = call_id
        self.timeout = timeout
        # The server rejects these routes without CALL_EVENTS_SECRET once it has one set.
        secret = os.getenv("CALL_EVENTS_SECRET", "").strip()
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)
        self._tasks: set[asyncio.Task] = set()
        self._last_partial = ("", 0.0)

    def post_line(self, speaker: str, text: str, final: bool = True) -> None:
        """Live transcript line for the dashboard (POST /calls/{id}/transcript). Fire and forget:
        never awaited by the call, errors are logged once at debug level. Partials are throttled."""
        text = (text or "").strip()
        if not text:
            return
        if not final:
            last_text, last_at = self._last_partial
            if text == last_text or time.monotonic() - last_at < 0.3:
                return
            self._last_partial = (text, time.monotonic())
        task = asyncio.create_task(self._post_line(speaker, text, final))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _post_line(self, speaker: str, text: str, final: bool) -> None:
        url = f"{self.server_url}/calls/{self.call_id}/transcript"
        try:
            r = await self._client.post(url, json={"speaker": speaker, "text": text, "final": final}, timeout=3.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.debug("transcript line not posted call=%s: %s", self.call_id, e)

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
        await self._client.aclose()
