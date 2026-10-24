"""Dashboard login by SMS one-time code, sent with Twilio.

POST /api/auth/otp     {"phone": "+49..."}             texts a 6-digit code
POST /api/auth/verify  {"phone": "+49...", "code": "..."} returns {"token": ...}

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
import time
from typing import Protocol

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from common.masking import mask_phone, mask_text
from server import settings

log = logging.getLogger("server.auth")
router = APIRouter(prefix="/api/auth")

CODE_TTL_S = 300
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_S = 30
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
    pass


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
        except httpx.HTTPError as exc:
            raise TwilioError(f"request failed: {exc}") from exc
        if r.status_code >= 400:
            # Twilio errors look like {"code": 21211, "message": "..."}; the message may hold the number.
            try:
                err = r.json()
                detail = f"{err.get('code')} {mask_text(str(err.get('message', '')))}"
            except ValueError:
                detail = r.text[:200]
            raise TwilioError(f"HTTP {r.status_code}: {detail}")
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
            r = await http.get(f"{base}/IncomingPhoneNumbers.json", params={"PhoneNumber": sender_number})
            numbers = r.json().get("incoming_phone_numbers", []) if r.status_code == 200 else []
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


# --- codes -------------------------------------------------------------------------

_codes: dict[str, dict] = {}        # phone -> {"hash", "expires", "attempts", "sent_at"}
_sends: dict[str, list[float]] = {}  # phone -> send times in the last hour


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


@router.post("/otp")
async def request_code(req: OtpRequest) -> dict:
    phone = _norm_phone(req.phone)
    now = time.time()
    prev = _codes.get(phone)
    if prev and now - prev["sent_at"] < RESEND_COOLDOWN_S:
        raise HTTPException(429, "Code just sent, wait a few seconds before asking again")
    recent = [t for t in _sends.get(phone, []) if now - t < 3600]
    if len(recent) >= MAX_SENDS_PER_PHONE_PER_HOUR:
        raise HTTPException(429, "Too many codes for this number, try again later")

    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        await sender().send_sms(phone, f"Your Jamie login code is {code}. It expires in 5 minutes.")
    except Exception:
        log.exception("could not send login code to %s", mask_phone(phone))
        raise HTTPException(502, "Could not send the code, try again in a moment")
    _codes[phone] = {"hash": _hash(phone, code), "expires": now + CODE_TTL_S, "attempts": 0, "sent_at": now}
    _sends[phone] = recent + [now]
    return {"sent": True, "expires_in": CODE_TTL_S}


@router.post("/verify")
async def verify_code(req: VerifyRequest) -> dict:
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
    log.info("login ok for %s", mask_phone(phone))
    return {"token": make_token(phone), "phone": phone}
