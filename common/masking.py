"""Log helpers: never log full phone numbers."""
import re

_PHONE_RE = re.compile(r"\+?\d[\d\s\-/()]{6,}\d")


def mask_phone(number: str) -> str:
    """Keep only the last 4 digits: '+4915112345678' -> '***5678'."""
    digits = re.sub(r"\D", "", number or "")
    return "***" + digits[-4:] if len(digits) > 4 else "***"


def mask_text(text: str) -> str:
    """Mask anything that looks like a phone number inside free text."""
    return _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), text or "")
