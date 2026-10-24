"""One-off provisioning for outbound calls (role A): Twilio Elastic SIP trunk + LiveKit outbound trunk.

    .venv/bin/python scripts/setup_trunks.py                # Twilio, then LiveKit; prints the trunk id for .env
    .venv/bin/python scripts/setup_trunks.py --livekit-only  # Twilio already configured in the console

Reads from .env: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_NUMBER, LIVEKIT_URL, LIVEKIT_API_KEY,
LIVEKIT_API_SECRET. Optional: SIP_TRUNK_NAME (default "jamie"), SIP_AUTH_USERNAME, SIP_AUTH_PASSWORD
(generated on first run and printed once; put them in .env so later runs reuse them).

Safe to re-run: it looks up each object by name before creating it. Twilio endpoints are from
https://www.twilio.com/docs/sip-trunking/api (Trunks, Trunk CredentialLists, Trunk PhoneNumbers) and
https://www.twilio.com/docs/voice/sip/api (SIP CredentialLists, Credentials).
"""
import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "call_agent"))
load_dotenv(ROOT / ".env")

from livekit import api  # noqa: E402
from livekit.protocol.sip import CreateSIPOutboundTrunkRequest, ListSIPOutboundTrunkRequest, SIPOutboundTrunkInfo  # noqa: E402

from events import mask_phone  # noqa: E402

TRUNKING = "https://trunking.twilio.com/v1"
REST = "https://api.twilio.com/2010-04-01/Accounts/{sid}"


def need(var: str) -> str:
    val = os.getenv(var)
    if not val:
        sys.exit(f"missing {var} in .env")
    return val


class Twilio:
    def __init__(self, sid: str, token: str):
        self.sid = sid
        self.http = httpx.Client(auth=(sid, token), timeout=20)
        self.rest = REST.format(sid=sid)

    def _ok(self, r: httpx.Response, what: str) -> dict:
        if r.status_code >= 400:
            sys.exit(f"twilio {what} failed: {r.status_code} {r.text[:300]}")
        return r.json()

    def trunk(self, name: str, domain: str) -> str:
        trunks = self._ok(self.http.get(f"{TRUNKING}/Trunks", params={"PageSize": 50}), "list trunks")["trunks"]
        for t in trunks:
            if t.get("domain_name") == domain:
                print(f"twilio trunk exists: {t['sid']} {domain}")
                return t["sid"]
        t = self._ok(self.http.post(f"{TRUNKING}/Trunks", data={"FriendlyName": name, "DomainName": domain}), "create trunk")
        print(f"twilio trunk created: {t['sid']} {domain}")
        return t["sid"]

    def credential_list(self, name: str, username: str, password: str) -> str:
        lists = self._ok(self.http.get(f"{self.rest}/SIP/CredentialLists.json", params={"PageSize": 50}), "list credlists")
        cl_sid = next((c["sid"] for c in lists.get("credential_lists", []) if c.get("friendly_name") == name), None)
        if cl_sid is None:
            cl = self._ok(self.http.post(f"{self.rest}/SIP/CredentialLists.json", data={"FriendlyName": name}), "create credlist")
            cl_sid = cl["sid"]
            print(f"twilio credential list created: {cl_sid}")
        else:
            print(f"twilio credential list exists: {cl_sid}")
        creds = self._ok(self.http.get(f"{self.rest}/SIP/CredentialLists/{cl_sid}/Credentials.json"), "list creds")
        if any(c.get("username") == username for c in creds.get("credentials", [])):
            print(f"twilio credential exists for user {username} (password not re-checked; must match .env)")
        else:
            self._ok(self.http.post(f"{self.rest}/SIP/CredentialLists/{cl_sid}/Credentials.json",
                                    data={"Username": username, "Password": password}), "create credential")
            print(f"twilio credential created for user {username}")
        return cl_sid

    def attach_credential_list(self, trunk_sid: str, cl_sid: str) -> None:
        r = self.http.post(f"{TRUNKING}/Trunks/{trunk_sid}/CredentialLists", data={"CredentialListSid": cl_sid})
        if r.status_code >= 400 and "already" not in r.text.lower():
            sys.exit(f"twilio attach credential list failed: {r.status_code} {r.text[:300]}")
        print("twilio credential list attached to trunk (termination auth)")

    def attach_number(self, trunk_sid: str, number: str) -> None:
        nums = self._ok(self.http.get(f"{self.rest}/IncomingPhoneNumbers.json", params={"PhoneNumber": number}), "find number")
        items = nums.get("incoming_phone_numbers", [])
        if not items:
            sys.exit(f"TWILIO_NUMBER {mask_phone(number)} is not owned by this account; buy it in the console first")
        pn_sid = items[0]["sid"]
        r = self.http.post(f"{TRUNKING}/Trunks/{trunk_sid}/PhoneNumbers", data={"PhoneNumberSid": pn_sid})
        if r.status_code >= 400 and "already" not in r.text.lower():
            sys.exit(f"twilio attach number failed: {r.status_code} {r.text[:300]}")
        print(f"twilio number {mask_phone(number)} attached to trunk")


async def livekit_trunk(name: str, domain: str, number: str, username: str, password: str) -> str:
    async with api.LiveKitAPI() as lk:
        existing = await lk.sip.list_sip_outbound_trunk(ListSIPOutboundTrunkRequest())
        for t in existing.items:
            if t.name == name:
                print(f"livekit outbound trunk exists: {t.sip_trunk_id} -> {t.address}")
                return t.sip_trunk_id
        info = SIPOutboundTrunkInfo(
            name=name,
            address=domain,
            numbers=[number],
            auth_username=username,
            auth_password=password,
            destination_country="DE",  # region pinning for German numbers; harmless otherwise
        )
        created = await lk.sip.create_sip_outbound_trunk(CreateSIPOutboundTrunkRequest(trunk=info))
        print(f"livekit outbound trunk created: {created.sip_trunk_id} -> {domain}")
        return created.sip_trunk_id


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--twilio-only", action="store_true")
    p.add_argument("--livekit-only", action="store_true")
    args = p.parse_args()

    name = os.getenv("SIP_TRUNK_NAME", "jamie")
    domain = f"{name}.pstn.twilio.com"
    number = need("TWILIO_NUMBER")
    username = os.getenv("SIP_AUTH_USERNAME") or f"{name}-livekit"
    password = os.getenv("SIP_AUTH_PASSWORD")
    generated = False
    if not password:
        password = secrets.token_urlsafe(18)
        generated = True

    if not args.livekit_only:
        tw = Twilio(need("TWILIO_ACCOUNT_SID"), need("TWILIO_AUTH_TOKEN"))
        trunk_sid = tw.trunk(name, domain)
        cl_sid = tw.credential_list(f"{name}-livekit", username, password)
        tw.attach_credential_list(trunk_sid, cl_sid)
        tw.attach_number(trunk_sid, number)

    if not args.twilio_only:
        for var in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
            need(var)
        trunk_id = asyncio.run(livekit_trunk(name, domain, number, username, password))
        print("\nadd to .env:")
        print(f"LIVEKIT_SIP_OUTBOUND_TRUNK_ID={trunk_id}")
    if generated:
        print("\ngenerated SIP credentials, add them to .env so re-runs reuse them:")
        print(f"SIP_AUTH_USERNAME={username}")
        print(f"SIP_AUTH_PASSWORD={password}")


if __name__ == "__main__":
    main()
