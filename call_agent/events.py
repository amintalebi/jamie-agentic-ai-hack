"""HTTP client that posts CallEvents to the server and long-polls for the user's answer."""
import logging

import httpx

from contracts_local import CallEvent  # TODO(hack): switch to server.contracts
from common.masking import mask_phone, mask_text  # noqa: F401  (contracts_local puts the repo root on sys.path)

log = logging.getLogger("call_agent.events")


class EventsClient:
    def __init__(self, server_url: str, call_id: str, timeout: float = 5.0):
        self.server_url = server_url.rstrip("/")
        self.call_id = call_id
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

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
