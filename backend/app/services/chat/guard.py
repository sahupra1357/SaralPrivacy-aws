"""Prompt-injection and jailbreak defence for the Setu chat endpoint.

Rewrite of ``frontend/lib/chat/guard.ts``, rule for rule.

Setu's product claim is that he is structurally incapable of inventing a DPDPA answer: he
speaks only from ``<retrieved_context>``. That guarantee rests entirely on the model being
able to tell OUR framing tags from the user's words. Everything in this file exists to
keep that line intact.

Threat model — three of the four inputs reaching the model are attacker controlled: the
message body, the client-supplied history, and the client-supplied session state
(factsConfirmed / pageUrl). Retrieved context is ours, EXCEPT the live briefings block,
which is generated from external news and must therefore be treated as untrusted too.

Layers, in the order a turn passes through them:

1. :func:`strip_invisible`  — kill zero-width / Unicode-tag smuggling
2. :func:`neutralize_tags`  — our tag vocabulary can never appear in user text
3. :func:`detect_injection` — obvious jailbreak intent refuses BEFORE the model
4. :func:`wrap_user_message` — nonce delimiter a forged closing tag cannot match
5. :func:`scan_output`      — the answer never leaks the instructions back out
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from app.core.config import settings

# ── 1. Invisible-character smuggling ──────────────────────────────────────────
# Unicode Tag characters (U+E0000–U+E007F) mirror ASCII and render as nothing. A payload
# written in them is invisible to a human reviewer and to most log viewers, but a model
# reads it as plain text. Zero-width and bidi controls are the same trick with wider tool
# support.
_INVISIBLE_RE = re.compile("[­​-‏‪-‮⁠-⁤⁪-⁯﻿]|[\U000e0000-\U000e007f]")


def strip_invisible(text: str) -> str:
    return _INVISIBLE_RE.sub("", text)


# ── 2. Control-tag neutralisation ─────────────────────────────────────────────
#: The framing vocabulary the model is trained by the system prompt to trust.
CONTROL_TAGS: tuple[str, ...] = (
    "user_message",
    "retrieved_context",
    "glossary_match",
    "facts_confirmed",
    "current_page",
    "journey",
    "industry",
    "note",
    "system",
    "instructions",
)

# Any tag-shaped token at all, not just ours: an attacker who invents <admin_override> is
# relying on the same "this looks like framing" effect.
_TAG_LIKE_RE = re.compile(r"<\s*/?\s*[A-Za-z_][A-Za-z0-9_:.\-]*(\s[^<>]*)?>")


def neutralize_tags(text: str) -> str:
    """Render tag-shaped tokens inert by escaping the angle brackets. The model still
    reads the user's words — it just reads them as literal text rather than as structure.
    Escaping beats stripping here: deleting the token would silently change what the user
    asked."""
    return _TAG_LIKE_RE.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), text)


def sanitize_untrusted(text: str) -> str:
    """Full cleaning pass for any untrusted free text heading for the model."""
    return neutralize_tags(strip_invisible(text))


def sanitize_inline(text: str, maximum: int = 200) -> str:
    """Cleaning pass for values placed in a TRUSTED position — ``<facts_confirmed>``,
    ``<current_page>``. These sit where the prompt treats content as established, so they
    additionally lose newlines: a value cannot open a new pseudo-block."""
    return re.sub(r"[\r\n]+", " ", sanitize_untrusted(text)).strip()[:maximum]


# ── 3. Injection-intent detection ─────────────────────────────────────────────
@dataclass(frozen=True)
class InjectionVerdict:
    blocked: bool
    rule: str | None = None


# Deliberately narrow. A false positive refuses a real compliance question, which is a
# worse outcome for this product than a jailbreak attempt that still has to get past every
# other layer. Each pattern targets phrasing that has no legitimate reading in a DPDPA help
# conversation.
INJECTION_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "override-previous",
        re.compile(
            r"\b(ignore|disregard|forget|override|discard)\b[^.?!]{0,40}"
            r"\b(previous|prior|earlier|above|all|any|your)\b[^.?!]{0,30}"
            r"\b(instruction|prompt|rule|direction|guideline|constraint|boundary)",
            re.I,
        ),
    ),
    (
        "reveal-instructions",
        re.compile(
            r"\b(reveal|show|print|repeat|output|display|reproduce|recite|dump|list|share|tell)\b"
            r"[^.?!]{0,40}\b(your|the|these|initial|original|full|exact|verbatim)\b[^.?!]{0,20}"
            r"\b(system\s*prompt|instruction|prompt|directive|ruleset|configuration|guidelines)",
            re.I,
        ),
    ),
    ("system-prompt-noun", re.compile(r"\b(system|initial|original|hidden)\s+prompt\b", re.I)),
    ("persona-override", re.compile(r"\byou\s+are\s+(now|no\s+longer)\b", re.I)),
    (
        "mode-override",
        re.compile(
            r"\b(developer|debug|god|admin|dan|jailbreak|unrestricted|uncensored|sudo)\s+mode\b",
            re.I,
        ),
    ),
    ("jailbreak-noun", re.compile(r"\bjailbroken?\b", re.I)),
    (
        "roleplay-escape",
        re.compile(
            r"\b(pretend|roleplay|role-play|act as|behave as|simulate)\b[^.?!]{0,50}"
            r"\b(no|not|without|free from|unrestricted|uncensored|unfiltered|no longer)\b",
            re.I,
        ),
    ),
    (
        "restriction-bypass",
        re.compile(
            r"\b(bypass|circumvent|get around|turn off|disable|switch off|remove)\b[^.?!]{0,30}"
            r"\b(restriction|guardrail|filter|safety|rule|limitation|boundary|safeguard)",
            re.I,
        ),
    ),
    (
        "ungrounded-answer",
        re.compile(
            r"\banswer\b[^.?!]{0,30}\b(from|using|with)\b[^.?!]{0,20}"
            r"\b(your\s+own|general|internal|training|pretrain)\b[^.?!]{0,15}\b(knowledge|memory|data)",
            re.I,
        ),
    ),
    (
        "new-instructions",
        re.compile(
            r"\b(new|updated|revised|additional)\s+(instruction|rule|system\s*prompt|directive)s?\s*[:\-]",
            re.I,
        ),
    ),
    (
        "from-now-on",
        re.compile(r"\bfrom\s+now\s+on\b[^.?!]{0,40}\b(you|ignore|answer|respond|act)\b", re.I),
    ),
    # A literal framing tag in user input is never innocent — the UI never sends one.
    ("control-tag-literal", re.compile(rf"<\s*/?\s*({'|'.join(CONTROL_TAGS)})\s*[^<>]*>", re.I)),
]


def detect_injection(message: str) -> InjectionVerdict:
    """Runs on the RAW message, before neutralisation — the point is to catch the attempt,
    and the attempt is most legible in its original form."""
    probe = strip_invisible(message)
    for rule_id, pattern in INJECTION_RULES:
        if pattern.search(probe):
            return InjectionVerdict(blocked=True, rule=rule_id)
    return InjectionVerdict(blocked=False)


# ── 4. Nonce-delimited user block ─────────────────────────────────────────────
def new_nonce() -> str:
    """Fresh per turn, so a delimiter cannot be forged from a previous response."""
    return secrets.token_hex(8)


def wrap_user_message(message: str, nonce: str) -> str:
    """The closing tag carries an unguessable nonce, so even if neutralisation were
    somehow evaded the user still cannot close the block early and start writing in the
    framework's voice."""
    return (
        f'<user_message id="{nonce}">\n{sanitize_untrusted(message)}\n</user_message id="{nonce}">'
    )


# ── 5. Output-side leak scan ──────────────────────────────────────────────────
# Distinctive strings from the system prompt. If any surfaces in an answer, the
# instructions are being read back to the user.
LEAK_SIGNATURES: tuple[str, ...] = (
    "hard boundary",
    "museum guide for this museum",
    "setu pro:",
    "navigation duty",
    "regulatory context (fixed",
    "<retrieved_context>",
    "<facts_confirmed>",
    "<user_message",
    "my system prompt",
    "my instructions are",
)

#: Longest signature — how far the stream must lag to never emit a full one.
LEAK_HOLDBACK = max(len(s) for s in LEAK_SIGNATURES)


@dataclass(frozen=True)
class LeakVerdict:
    leaked: bool
    rule: str | None = None


def scan_output(text: str) -> LeakVerdict:
    lowered = text.lower()
    for sig in LEAK_SIGNATURES:
        if sig in lowered:
            return LeakVerdict(leaked=True, rule=sig)
    return LeakVerdict(leaked=False)


# ── 6. Assistant-turn authenticity ────────────────────────────────────────────
# History arrives from the client, so an attacker can forge an assistant turn ("developer
# mode enabled") and the model reads it as its own prior words — far more persuasive than
# any user instruction. Signing each answer we emit lets the next turn tell our transcript
# from theirs, with no session store.
SIG_LENGTH = 32


def _secret() -> str:
    return settings.CHAT_HISTORY_SECRET


def history_signing_available() -> bool:
    return len(_secret()) > 0


def sign_turn(text: str) -> str:
    secret = _secret()
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()[
        :SIG_LENGTH
    ]


def verify_turn(text: str, sig: object) -> bool:
    if not _secret() or not isinstance(sig, str) or len(sig) != SIG_LENGTH:
        return False
    return hmac.compare_digest(sign_turn(text), sig)
