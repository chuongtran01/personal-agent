"""The sensitive-data filter: what is never stored as a memory, whatever the model proposed.

The extractor is told not to propose secrets, but the instruction is a request and this is the
guarantee. It runs in code after extraction and before anything else sees a proposal: an ``add``
that looks like a card number, a bank account, a password or PIN, a key or token, or an
identifier next to the word for it is dropped, and the rest of the turn goes on (the trip is kept,
the passport number is not).

It leans towards dropping. A memory it wrongly drops ("the owner is bad at remembering passwords")
costs a harmless fact; one it wrongly keeps costs a card number on file. A ``delete`` is never
dropped: "forget my card number" must still find and remove a number that slipped in earlier.

A dropped proposal is reported by the rule that caught it, never by its text, so neither the log
nor the result keeps what was filtered.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.memory.extract import Proposal

log = logging.getLogger(__name__)

# Sixteen digits in fours (and the American Express shape), or any unbroken run of 13 or more
_CARD = re.compile(
    r"(?<![\d-])(?:\d{4}(?:[ -]\d{4}){3,4}|\d{4}[ -]\d{6}[ -]\d{5}|\d{13,})(?![\d-])"
)
# Two capital letters, two check digits, then the account, in groups of four or not
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")
_SECRET_WORD = re.compile(
    r"\b(?:passwords?|passcodes?|passphrases?|pins?|cvv|cvc|otp|secrets?"
    r"|(?:api|private|secret|access)[ _-]?(?:keys?|tokens?)"
    r"|(?:security|recovery|backup)[ _-]?codes?)\b",
    re.IGNORECASE,
)
# A long token with both letters and digits (a key), or a long run of hex (a hash or secret)
_KEY = re.compile(
    r"\b(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{20,}\b|\b[0-9a-fA-F]{32,}\b"
)
_ID_WORD = re.compile(
    r"\b(?:passport|ssn|social[ -]security|national[ -]id|id[ -]card|identity[ -]card"
    r"|tax[ -](?:id|number)|nif|nie|cpf|driver'?s[ -]licen[cs]e|licen[cs]e[ -]number)\b",
    re.IGNORECASE,
)
# A few letters and then six or more digits, or the 3-2-4 shape of a social security number
_ID_TOKEN = re.compile(r"\b[A-Za-z]{0,3}\d{6,}\b|\b\d{3}-\d{2}-\d{4}\b")


def _luhn(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def sensitivity(text: str) -> str | None:
    """The rule a text breaks, or None. Rules are checked in a fixed order and the first wins."""
    for found in _CARD.finditer(text):
        digits = re.sub(r"\D", "", found.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return "card"
    if _IBAN.search(text):
        return "iban"
    if _CARD.search(text):
        return "long_number"
    if _SECRET_WORD.search(text):
        return "secret_word"
    if _KEY.search(text):
        return "key"
    if _ID_WORD.search(text) and _ID_TOKEN.search(text):
        return "identifier"
    return None


@dataclass(frozen=True)
class Filtered:
    kept: tuple[Proposal, ...]
    dropped: tuple[str, ...]  # the rule that dropped each proposal, never its text


def filter_proposals(proposals: Sequence[Proposal]) -> Filtered:
    """Drop the adds that look sensitive. Deletes always pass."""
    kept: list[Proposal] = []
    dropped: list[str] = []
    for proposal in proposals:
        rule = sensitivity(proposal.content) if proposal.op == "add" else None
        if rule is None:
            kept.append(proposal)
        else:
            dropped.append(rule)
    if dropped:
        log.info("memory filter dropped %d proposal(s): %s", len(dropped), ", ".join(dropped))
    return Filtered(tuple(kept), tuple(dropped))
