"""Phone-call style layer appended to role E's system prompt (role A owns this file).

E's prompts.py decides WHAT the agent says (goal, rules, tools). This decides HOW it sounds on a
phone: short turns, listen first, one summary, and optional ElevenLabs audio tags for emotion.
Audio tags are inline square-bracket cues that Eleven v4 Turbo renders (for example [warm],
[apologetic], [laughs]); they lengthen delivery a little, so replies stay short. Enabled with
CALL_AUDIO_TAGS=1. agent.py strips the tags from the transcript it ships to the server.
"""
import os
import re

from contracts_local import CallBrief, CallEvent  # noqa: F401  TODO(hack): switch to common.contracts

TAG_RE = re.compile(r"\[[^\]\n]{1,40}\]")

# Small, safe set. Anything else the model invents still renders on v4, but these are tested.
TAGS = ["[warm]", "[cheerful]", "[apologetic]", "[thoughtful]", "[reassuring]", "[laughs]"]


def audio_tags_enabled() -> bool:
    return os.getenv("CALL_AUDIO_TAGS", "0").strip().lower() in ("1", "true", "yes", "on")


def strip_tags(text: str) -> str:
    """'[warm] Hi there' -> 'Hi there'. Used for transcripts and logs."""
    return re.sub(r"\s{2,}", " ", TAG_RE.sub("", text)).strip()


def style_addendum(brief: CallBrief) -> str:
    name = brief.caller_name
    if brief.language == "de":
        base = f"""

# Telefonstil (gilt zusätzlich zu allem oben)
- Hör zuerst zu. Das Gegenüber begrüßt dich; antworte erst dann.
- Deine erste Antwort: die Begrüßung plus ein kurzer Satz mit dem Kern des Anliegens (zum Beispiel ein Tisch für vier am Sonntagabend). Details wie Uhrzeit, Platz oder Name kommen, wenn danach gefragt wird.
- Ein Satz pro Antwort, höchstens zwei, jeder unter fünfzehn Wörtern. Einzige Ausnahme: die eine Zusammenfassung am Ende.
- Kurze Bestätigungen sind gut: "Gerne.", "Alles klar.", "Danke."
- Wiederhole dich nicht. Keine Floskeln wie "Vielen Dank für die Nachfrage"."""
    else:
        base = f"""

# Phone style (applies on top of everything above)
- Listen first. The other person greets you; only then reply.
- Your first reply: the greeting plus one short sentence with the core request (for example a table for four on Sunday evening). Details like the exact time, seating or the name come when they ask.
- One sentence per reply, two at most, each under fifteen words. The single summary at the end is the only exception.
- Short acknowledgements are good: "Sure.", "Got it.", "Thanks."
- Do not repeat yourself. No filler like "I appreciate you checking"."""
    if not audio_tags_enabled():
        return base
    tags = " ".join(TAGS)
    if brief.language == "de":
        return base + f"""
- Stimmung: Setze höchstens einen Ton-Tag ganz an den Anfang einer Antwort, in eckigen Klammern, einen von: {tags}. Zum Beispiel "[warm] Hallo, guten Tag, ich rufe für {name} an." Nie in ask_user oder end_call, nie mitten im Satz. Weglassen, wenn neutral."""
    return base + f"""
- Mood: put at most one tone tag at the very start of a reply, in square brackets, one of: {tags}. For example "[warm] Hi, good day, I'm calling on behalf of {name}." Never inside ask_user or end_call, never mid-sentence. Leave it out when neutral."""
