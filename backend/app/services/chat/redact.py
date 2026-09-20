"""PII redaction for chat logs — spec §7 (v2.4). Rewrite of frontend/lib/chat/redact.ts.

Applied before ANYTHING chat-related is persisted server-side: failure-turn questions in
``chat_feedback`` (D2) and the human-handoff packet (§9.5). Redaction is deliberately
eager: false positives cost readability in an internal log; false negatives put PII in
storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: Aadhaar before generic phone (both are digit runs).
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("[email]", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("[aadhaar]", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("[pan]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("[phone]", re.compile(r"\+?\d[\d\s-]{8,}\d")),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redactions: int


def redact(text: str) -> RedactionResult:
    out = text
    count = 0
    for label, pattern in _PATTERNS:
        out, n = pattern.subn(label, out)
        count += n
    return RedactionResult(text=out, redactions=count)


def redact_text(text: str) -> str:
    """Convenience for log writers — redacted text only."""
    return redact(text).text
