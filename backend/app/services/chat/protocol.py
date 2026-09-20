"""Two-phase stream protocol (spec §5.5) shared by POST /api/v1/chat and the widget.

Phase A: plain text tokens. Phase B: ``META_SENTINEL`` + the ChatMeta JSON object.

The constant must stay byte-identical to ``frontend/lib/chat/protocol.ts``; the widget
splits the response body on it.
"""

#: U+001E RECORD SEPARATOR — never appears in prose or JSON.
META_SENTINEL = ""
