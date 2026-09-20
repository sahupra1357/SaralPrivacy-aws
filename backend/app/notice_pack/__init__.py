"""Notice Pack Builder — the server half of `frontend/lib/notice-pack`.

Only what the PDF route needs: the deterministic notice document (`render`) and the
sector / context / vendor tables it reads (`data`). Scoring, risk flags, evidence
records and the wizard's data-group catalogue stay in TypeScript — they are UI logic.

This package is private to the notices module; nothing outside `app/api/routes/notices.py`
and `app/tests/notices/` imports it.
"""
