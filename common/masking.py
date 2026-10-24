"""Log helpers: never log full phone numbers."""
import re


def mask_phone(number: str) -> str:
    """'+4915112345678' -> '***5678'."""
    digits = re.sub(r"\D", "", number or "")
    return "***" + digits[-4:] if len(digits) > 4 else "***"
