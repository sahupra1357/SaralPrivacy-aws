"""`NPState` from `frontend/lib/notice-pack/types.ts`, with the PDF route's clamps.

The zod schema in `app/api/notice/pdf/route.ts` is permissive on purpose — we only render
the user's own answers back to them — but it clamps every size so the render stays fast
and the payload sane. Over-long values are *rejected* (as zod did), not truncated.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

Lang = Literal["en", "hi"]


class NoticeState(BaseModel):
    org: Annotated[str, Field(max_length=160)] = ""
    website: Annotated[str, Field(max_length=200)] = ""
    sector: Annotated[str, Field(max_length=80)] = ""
    data: Annotated[list[Annotated[str, Field(max_length=120)]], Field(max_length=80)] = []
    contexts: Annotated[list[Annotated[str, Field(max_length=60)]], Field(max_length=40)] = []
    purpose: dict[str, Annotated[str, Field(max_length=400)]] = {}
    consentVia: Annotated[list[Annotated[str, Field(max_length=80)]], Field(max_length=20)] = []
    withdrawMethod: Annotated[list[Annotated[str, Field(max_length=80)]], Field(max_length=20)] = []
    withdrawContact: Annotated[str, Field(max_length=200)] = ""
    vendors: Annotated[list[Annotated[str, Field(max_length=120)]], Field(max_length=40)] = []
    noVendors: bool = False
    children: Literal["", "Yes", "No", "Not sure"] = ""
    childWhy: Annotated[list[Annotated[str, Field(max_length=120)]], Field(max_length=20)] = []
    retention: Annotated[str, Field(max_length=60)] = ""
    cName: Annotated[str, Field(max_length=120)] = ""
    cEmail: Annotated[str, Field(max_length=160)] = ""
    cPhone: Annotated[str, Field(max_length=40)] = ""
    regAddress: Annotated[str, Field(max_length=240)] = ""
    slug: Annotated[str, Field(max_length=80)] = ""
    lang: Lang = "en"
