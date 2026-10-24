"""Signup and login by phone number: an SMS one-time code sent with Twilio.

POST /api/auth/otp     {"phone": "+49..."}             texts a 6-digit code, returns {"retry_after": s}
POST /api/auth/verify  {"phone": "+49...", "code": "..."} returns {"token", "phone", "new_user"}

The first verified login creates the user (server/users.py) and sends a welcome iMessage via Linq.
Sending retries Twilio hiccups (network, 429, 5xx). Each resend waits longer (RESEND_COOLDOWNS_S);
a 429 carries Retry-After so the app can count down.

The token is stateless (HMAC-signed phone + expiry), so it survives restarts as long as
AUTH_SECRET stays the same. Codes live in memory: a restart only voids codes in flight.
SMS_MODE=fake logs the code instead of sending it.
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
import asyncio
import time
from typing import Protocol

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from common.masking import mask_phone, mask_text
from server import settings, users

log = logging.getLogger("server.auth")
router = APIRouter(prefix="/api/auth")

CODE_TTL_S = 300
MAX_ATTEMPTS = 5
RESEND_COOLDOWNS_S = (30, 60, 120, 300)  # wait after the 1st, 2nd, 3rd, 4th+ code this hour
SEND_RETRY_DELAYS_S = (1, 3)              # Twilio retries after a transient failure
MAX_SENDS_PER_PHONE_PER_HOUR = 5
TOKEN_TTL_S = 30 * 24 * 3600
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

if settings.AUTH_SECRET:
    _secret = settings.AUTH_SECRET.encode()
else:
    # TODO(hack): set AUTH_SECRET in .env, otherwise every restart signs everyone out.
    _secret = secrets.token_bytes(32)
    log.warning("AUTH_SECRET not set, using a random one: sessions end when the server restarts")


# --- SMS adapters ------------------------------------------------------------------

class SmsSender(Protocol):
    async def send_sms(self, to: str, body: str) -> None: ...


class FakeSmsSender:
    async def send_sms(self, to: str, body: str) -> None:
        log.info("[fake send_sms] to %s: %s", mask_phone(to), body)


class TwilioError(Exception):
    def __init__(self, msg: str, transient: bool = False, maybe_sent: bool = False) -> None:
        super().__init__(msg)
        self.transient = transient    # Twilio surely did not take the message: safe to retry
        self.maybe_sent = maybe_sent  # the reply got lost, Twilio may have sent it: never retry


class TwilioSmsSender:
    """Twilio Messages API: https://www.twilio.com/docs/messaging/api/message-resource#create-a-message-resource"""

    def __init__(self) -> None:
        sid, token = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN
        if not (sid and token and (settings.TWILIO_SMS_FROM or settings.TWILIO_MESSAGING_SERVICE_SID)):
            raise RuntimeError("SMS_MODE=twilio needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and "
                               "TWILIO_SMS_FROM (or TWILIO_NUMBER) or TWILIO_MESSAGING_SERVICE_SID")
        self._url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        self._http = httpx.AsyncClient(timeout=10, auth=(sid, token))

    async def send_sms(self, to: str, body: str) -> None:
        data = {"To": to, "Body": body}
        if settings.TWILIO_MESSAGING_SERVICE_SID:
            data["MessagingServiceSid"] = settings.TWILIO_MESSAGING_SERVICE_SID
        else:
            data["From"] = settings.TWILIO_SMS_FROM
        try:
            r = await self._http.post(self._url, data=data)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise TwilioError(f"could not reach Twilio: {exc}", transient=True) from exc
        except httpx.HTTPError as exc:
            # Sent, but no (full) reply, e.g. a read timeout: a retry could text the user twice.
            raise TwilioError(f"no reply from Twilio: {exc}", maybe_sent=True) from exc
        if r.status_code >= 400:
            # Twilio errors look like {"code": 21211, "message": "..."}; the message may hold the number.
            try:
                err = r.json()
                detail = f"{err.get('code')} {mask_text(str(err.get('message', '')))}"
            except ValueError:
                detail = r.text[:200]
            raise TwilioError(f"HTTP {r.status_code}: {detail}",
                              transient=r.status_code == 429 or r.status_code >= 500)
        log.info("sent sms to %s (sid %s)", mask_phone(to), r.json().get("sid"))


async def check_connection() -> bool:
    """Startup check: log whether Twilio is reachable, the credentials work and the sender can send SMS.
    Never raises, so a Twilio problem cannot stop the server from starting."""
    if settings.SMS_MODE != "twilio":
        log.info("sms: fake mode, login codes go to the log (no Twilio check)")
        return True
    sid, token = settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN
    if not (sid and token):
        log.error("sms: Twilio connection FAILED: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN missing")
        return False
    base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    try:
        async with httpx.AsyncClient(timeout=10, auth=(sid, token)) as http:
            r = await http.get(f"{base}.json")
            if r.status_code != 200:
                log.error("sms: Twilio connection FAILED: HTTP %s on account lookup (check SID / auth token)",
                          r.status_code)
                return False
            status = r.json().get("status")
            if status != "active":
                log.error("sms: Twilio connection FAILED: account status is %r", status)
                return False

            if settings.TWILIO_MESSAGING_SERVICE_SID:
                r = await http.get(f"https://messaging.twilio.com/v1/Services/{settings.TWILIO_MESSAGING_SERVICE_SID}")
                if r.status_code != 200:
                    log.error("sms: Twilio connected, but messaging service lookup FAILED: HTTP %s", r.status_code)
                    return False
                log.info("sms: Twilio connection OK, sending via messaging service %r", r.json().get("friendly_name"))
                return True

            sender_number = settings.TWILIO_SMS_FROM
            if not sender_number:
                log.error("sms: Twilio connected, but no sender: set TWILIO_NUMBER")
                return False
            # Match locally rather than with ?PhoneNumber=: httpx logs request URLs, and the number must stay masked.
            r = await http.get(f"{base}/IncomingPhoneNumbers.json", params={"PageSize": 1000})
            listed = r.json().get("incoming_phone_numbers", []) if r.status_code == 200 else []
            numbers = [n for n in listed if n.get("phone_number") == sender_number]
            if not numbers:
                log.error("sms: Twilio connected, but sender %s is not a number on this account",
                          mask_phone(sender_number))
                return False
            if not numbers[0].get("capabilities", {}).get("sms"):
                log.error("sms: Twilio connected, but sender %s CANNOT send SMS (voice only?); "
                          "set an SMS-capable TWILIO_SMS_FROM or TWILIO_MESSAGING_SERVICE_SID",
                          mask_phone(sender_number))
                return False
    except (httpx.HTTPError, ValueError) as exc:
        log.error("sms: Twilio connection FAILED: %s", exc)
        return False
    log.info("sms: Twilio connection OK, sending from %s", mask_phone(sender_number))
    return True


_sender: SmsSender | None = None


def sender() -> SmsSender:
    global _sender
    if _sender is None:
        _sender = TwilioSmsSender() if settings.SMS_MODE == "twilio" else FakeSmsSender()
    return _sender


async def send_with_retry(to: str, body: str) -> None:
    """Send, retrying only failures where Twilio surely did not take the message (no connection,
    429, 5xx). Errors like an invalid number, or a lost reply, are raised at once."""
    for attempt in range(len(SEND_RETRY_DELAYS_S) + 1):
        try:
            await sender().send_sms(to, body)
            return
        except TwilioError as exc:
            if not exc.transient or attempt == len(SEND_RETRY_DELAYS_S):
                raise
            delay = SEND_RETRY_DELAYS_S[attempt]
            log.warning("sms to %s failed (%s), retry %d in %ss", mask_phone(to), exc, attempt + 1, delay)
            await asyncio.sleep(delay)


# --- codes -------------------------------------------------------------------------

_codes: dict[str, dict] = {}        # phone -> {"hash", "expires", "attempts", "next_send_at"}
_sends: dict[str, list[float]] = {}  # phone -> send times in the last hour
_sending: set[str] = set()           # phones with a send in flight (sends with retries take seconds)


def _hash(phone: str, code: str) -> str:
    return hmac.new(_secret, f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def _norm_phone(raw: str) -> str:
    s = re.sub(r"[\s\-().]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not E164_RE.match(s):
        raise HTTPException(400, "Enter your number in international format, like +49 151 0000 1234")
    return s


# --- tokens ------------------------------------------------------------------------

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_token(phone: str, now: float | None = None) -> str:
    payload = f"{phone}|{int((now or time.time()) + TOKEN_TTL_S)}".encode()
    sig = hmac.new(_secret, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def read_token(token: str, now: float | None = None) -> str | None:
    """The phone number a valid token belongs to, or None."""
    try:
        p, s = token.split(".", 1)
        payload = base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))
        sig = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        if not hmac.compare_digest(sig, hmac.new(_secret, payload, hashlib.sha256).digest()):
            return None
        phone, exp = payload.decode().split("|")
        return phone if int(exp) > (now or time.time()) else None
    except (ValueError, UnicodeDecodeError):
        return None


async def current_user(authorization: str = Header(default="")) -> str:
    """FastAPI dependency: the logged-in phone number, or 401."""
    scheme, _, token = authorization.partition(" ")
    phone = read_token(token.strip()) if scheme.lower() == "bearer" else None
    if not phone:
        raise HTTPException(401, "not logged in")
    return phone


# --- routes ------------------------------------------------------------------------

class OtpRequest(BaseModel):
    phone: str


class VerifyRequest(BaseModel):
    phone: str
    code: str


def sms_body(code: str) -> str:
    """The login SMS. With APP_DOMAIN set, the last line "@host #code" binds the code to the PWA's
    origin: iOS Safari offers it above the keyboard and Android Chrome fills it via WebOTP."""
    body = f"Your Eve login code is {code}. It expires in 5 minutes."
    domain = settings.APP_DOMAIN.removeprefix("https://").removeprefix("http://").split("/")[0]
    return f"{body}\n\n@{domain} #{code}" if domain else body


def _too_soon(seconds: float, msg: str) -> HTTPException:
    wait = max(1, int(seconds + 0.999))
    return HTTPException(429, msg.format(wait=wait), headers={"Retry-After": str(wait)})


@router.post("/otp")
async def request_code(req: OtpRequest) -> dict:
    phone = _norm_phone(req.phone)
    now = time.time()
    prev = _codes.get(phone)
    if prev and now < prev["next_send_at"]:
        raise _too_soon(prev["next_send_at"] - now, "Code just sent, you can ask for a new one in {wait} s")
    recent = [t for t in _sends.get(phone, []) if now - t < 3600]
    if len(recent) >= MAX_SENDS_PER_PHONE_PER_HOUR:
        raise _too_soon(recent[0] + 3600 - now, "Too many codes for this number, try again in {wait} s")

    # One send per number at a time: the checks above and the writes below are seconds apart.
    if phone in _sending:
        raise _too_soon(5, "A code is on its way, wait {wait} s before asking again")
    _sending.add(phone)
    try:
        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            await send_with_retry(phone, sms_body(code))
        except TwilioError as exc:
            if not exc.maybe_sent:
                log.error("could not send login code to %s: %s", mask_phone(phone), exc)
                raise HTTPException(502, "Could not send the code, try again in a moment")
            # The SMS may be on its way: keep its code valid and treat it as sent.
            log.warning("login code to %s may have been sent (%s), keeping it valid", mask_phone(phone), exc)
        except Exception:
            log.exception("could not send login code to %s", mask_phone(phone))
            raise HTTPException(502, "Could not send the code, try again in a moment")
        return _code_sent(phone, code, now, recent)
    finally:
        _sending.discard(phone)


def _code_sent(phone: str, code: str, now: float, recent: list[float]) -> dict:
    # A failed send starts no cooldown; a successful one waits longer each time this hour.
    cooldown = RESEND_COOLDOWNS_S[min(len(recent), len(RESEND_COOLDOWNS_S) - 1)]
    _codes[phone] = {"hash": _hash(phone, code), "expires": now + CODE_TTL_S, "attempts": 0,
                     "next_send_at": now + cooldown}
    _sends[phone] = recent + [now]
    log.info("login code sent to %s (send %d this hour, next allowed in %ss)", mask_phone(phone),
             len(recent) + 1, cooldown)
    return {"sent": True, "expires_in": CODE_TTL_S, "retry_after": cooldown}


@router.post("/verify")
async def verify_code(req: VerifyRequest, background: BackgroundTasks) -> dict:
    phone = _norm_phone(req.phone)
    entry = _codes.get(phone)
    if not entry or entry["expires"] < time.time():
        _codes.pop(phone, None)
        raise HTTPException(400, "Code expired, ask for a new one")
    entry["attempts"] += 1
    if entry["attempts"] > MAX_ATTEMPTS:
        _codes.pop(phone, None)
        raise HTTPException(400, "Too many wrong tries, ask for a new code")
    if not hmac.compare_digest(entry["hash"], _hash(phone, req.code.strip())):
        raise HTTPException(400, "Wrong code")
    _codes.pop(phone, None)
    user = await users.login(phone)
    log.info("%s ok for %s", "signup" if user["new"] else "login", mask_phone(phone))
    if not user.get("activated_at"):
        background.add_task(users.activate, phone)  # welcome iMessage, after the response
    return {"token": make_token(phone), "phone": phone, "new_user": bool(user["new"])}
