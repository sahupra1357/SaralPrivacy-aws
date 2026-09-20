"""The user-facing strings the *server* emits, copied verbatim from
``frontend/lib/chat/strings.ts`` (decision D1).

The widget keeps its own full table — every component calls ``t(locale, key)`` there and
nothing about that changes. Only the seven strings a response body can contain live here,
so the two halves are small enough to keep in step by eye. If one of these is edited in
`strings.ts`, edit it here too.
"""

from typing import Literal

Locale = Literal["en", "hi"]

_EN: dict[str, str] = {
    "refusal": "I can only help with what's on SaralPrivacy — I don't have that in our guides yet.",
    "refusalHint": "Try the FAQ, the Learning Hub, or ask our team directly.",
    # Shown when a turn is blocked as an instruction-override attempt. Stays in Setu's
    # voice on purpose: no accusation, no hint about what tripped it.
    "guarded": (
        "I stay on DPDPA questions answered from SaralPrivacy's own guides — "
        "that's the only way I can be sure of what I tell you."
    ),
    "rateLimited": "You've asked a lot — give me a minute and try again.",
    "apiError": "Something went wrong on my side — please try that again.",
    "disclaimer": "Educational only — not legal advice.",
}

_HI: dict[str, str] = {
    "disclaimer": "केवल शैक्षिक — कानूनी सलाह नहीं।",
}

_LOCALES: dict[str, dict[str, str]] = {"en": _EN, "hi": _HI}


def t(locale: Locale, key: str) -> str:
    return _LOCALES.get(locale, _EN).get(key) or _EN[key]
