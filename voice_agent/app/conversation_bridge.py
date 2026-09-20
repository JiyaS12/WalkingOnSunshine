"""Bounded, model-written acknowledgments; never survey or clinical content.

The model composes a short sentence, not an enum of canned responses. A small
non-clinical vocabulary keeps this optional field separate from the question,
answer labels, advice, identifiers, promises, and free-form patient quotations.
This lexical check is a scope limit, not a proof of semantic correctness.
"""

import re


BRIDGE_WORDS = frozenset("""okay ok thanks thank you for telling sharing explaining clarifying that this
with me i i'm we're let's appreciate understand hear heard listening taking
your time take at own pace helping help get it clearer clear now a little
better what mean meant letting know and so of course sorry sounds difficult
hard tough been dealing going through hear explaining understood got
to really helps clearing up
""".split())


def validated_bridge(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("’", "'")
    # One brief declarative sentence. No questions, numbers, markup, URLs,
    # commands, options, promises to act, or unrestricted reflected symptoms.
    if not text or len(text) > 120 or not re.fullmatch(r"[A-Za-z' ,]+\.?", text):
        return None
    words = re.findall(r"[a-z]+(?:'[a-z]+)?", text.casefold())
    if not 1 <= len(words) <= 18 or not set(words) <= BRIDGE_WORDS:
        return None
    return text.rstrip(".") + "."
