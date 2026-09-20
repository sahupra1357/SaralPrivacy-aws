"""Deterministic brand infographic for blog posts: SVG layout per lane, rasterised to PNG.

Rewritten from `frontend/app/api/blog/infographic/route.ts`. The TS route rasterised with
@resvg/resvg-js and bundled Inter fonts; here Chromium (the same Playwright install the PDF
service uses) screenshots the SVG with Inter embedded via @font-face, so text renders
identically regardless of system fonts. PNG (not SVG) because object storage / browsers
refuse SVG in <img> when served as text/plain.
"""

import base64
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

W, H, PAD = 1200, 630, 60

BRAND = {
    "navy": "#121A2E",
    "green": "#07B981",
    "teal": "#35B6AE",
    "gold": "#E8AB42",
    "slate": "#334155",
    "cloud": "#F7F9FC",
    "white": "#FFFFFF",
    "mute": "#94A3B8",
}

LayoutId = Literal["stat", "process", "comparison", "checklist", "timeline"]

LANE_LAYOUT: dict[str, LayoutId] = {
    "law-explained": "stat",
    "compliance-playbook": "process",
    "myth-fact": "comparison",
    "sector-notes": "checklist",
    "governance-watch": "timeline",
}
DEFAULT_LAYOUT: LayoutId = "stat"

FONT_DIR = Path(__file__).resolve().parent / "fonts"


@dataclass(frozen=True)
class InfographicInput:
    title: str
    lane: str = ""
    excerpt: str | None = None
    section_what_changed: str | None = None
    section_law_says: str | None = None
    section_do_now: str | None = None


# ── primitives ─────────────────────────────────────────────────────────────
def esc(s: str | None) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _num(n: float) -> str:
    """Render numbers the way JS template literals do (40 not 40.0)."""
    return str(int(n)) if float(n).is_integer() else repr(float(n))


def wrap_text(text: str | None, max_chars: int, max_lines: int) -> list[str]:
    """Approximate character-budget wrapper (SVG <text> has no auto-wrap)."""
    words = [w for w in (text or "").strip().split() if w]
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(lines) >= max_lines:
            break
        nxt = f"{cur} {w}" if cur else w
        if len(nxt) <= max_chars:
            cur = nxt
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur and len(lines) < max_lines:
        lines.append(cur)
    used = len([w for w in " ".join(lines).split() if w])
    if used < len(words) and lines:
        lines[-1] = re.sub(r"[\s.,;:!?]*$", "", lines[-1]) + "…"
    return lines


_BULLET_PREFIX = re.compile(r"^[\s•·▪◦\-*\d.)]+")


def split_bullets(content: str | None, max_items: int) -> list[str]:
    lines = [_BULLET_PREFIX.sub("", ln).strip() for ln in re.split(r"\r?\n+", content or "")]
    return [ln for ln in lines if ln][:max_items]


def text_wrapped(
    s: str,
    *,
    x: float,
    y: float,
    max_chars: int,
    max_lines: int,
    line_height: float,
    fill: str,
    size: int,
    weight: int = 400,
    anchor: str | None = None,
) -> str:
    lines = wrap_text(s, max_chars, max_lines)
    if not lines:
        return ""
    tspans = "".join(
        f'<tspan x="{_num(x)}" dy="{0 if i == 0 else _num(line_height)}">{esc(ln)}</tspan>'
        for i, ln in enumerate(lines)
    )
    anchor_attr = f' text-anchor="{anchor}"' if anchor else ""
    return (
        f'<text x="{_num(x)}" y="{_num(y)}" font-family="Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}"{anchor_attr}>{tspans}</text>'
    )


# ── chrome ─────────────────────────────────────────────────────────────────
def frame(bg: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">
  <title>SaralPrivacy — Verified DPDPA insights</title>
  <rect width="{W}" height="{H}" fill="{bg}"/>
  {body}
</svg>"""


def title_block(title: str, color: str) -> str:
    return text_wrapped(
        title,
        x=PAD,
        y=PAD + 36,
        max_chars=50,
        max_lines=2,
        line_height=44,
        fill=color,
        size=34,
        weight=700,
    )


def footer(on_dark: bool) -> str:
    txt = BRAND["cloud"] if on_dark else BRAND["slate"]
    return f"""
  <line x1="{PAD}" y1="{H - 60}" x2="{W - PAD}" y2="{H - 60}" stroke="{BRAND["green"]}" stroke-width="2"/>
  <text x="{PAD}" y="{H - 28}" font-family="Inter, system-ui, sans-serif" font-size="14" font-weight="700" fill="{txt}">© SaralPrivacy</text>
  <text x="{W - PAD}" y="{H - 28}" font-family="Inter, system-ui, sans-serif" font-size="13" font-weight="500" fill="{BRAND["mute"]}" text-anchor="end">Verified DPDPA insights  ·  saralprivacy.com</text>
  """


# ── layouts ────────────────────────────────────────────────────────────────
def layout_stat(inp: InfographicInput) -> str:
    bullets = [
        *split_bullets(inp.section_what_changed, 1),
        *split_bullets(inp.section_law_says, 1),
        *split_bullets(inp.section_do_now, 1),
    ]
    while len(bullets) < 3:
        bullets.append("")
    labels = ["What changed", "What the law says", "What to do now"]
    gap, card_h, card_y = 24, 330, 190
    card_w = (W - PAD * 2 - gap * 2) / 3
    cards = ""
    for i, b in enumerate(bullets[:3]):
        x = PAD + i * (card_w + gap)
        cards += f"""
    <rect x="{_num(x)}" y="{card_y}" width="{_num(card_w)}" height="{card_h}" fill="{BRAND["cloud"]}" rx="12" ry="12"/>
    <rect x="{_num(x)}" y="{card_y}" width="{_num(card_w)}" height="6" fill="{BRAND["green"]}" rx="3" ry="3"/>
    <text x="{_num(x + 24)}" y="{card_y + 50}" font-family="Inter, system-ui, sans-serif" font-size="12" font-weight="700" fill="{BRAND["green"]}" letter-spacing="2">{esc(labels[i].upper())}</text>
    {text_wrapped(b, x=x + 24, y=card_y + 90, max_chars=30, max_lines=8, line_height=26, fill=BRAND["navy"], size=17, weight=500)}"""
    return frame(BRAND["navy"], title_block(inp.title, BRAND["white"]) + cards + footer(True))


def layout_process(inp: InfographicInput) -> str:
    items = [*split_bullets(inp.section_do_now, 5), *split_bullets(inp.section_what_changed, 5)][:5]
    while len(items) < 3:
        items.append("")
    steps, step_gap, start_y = len(items), 16, 200
    step_h = (H - start_y - 90 - step_gap * (steps - 1)) / steps
    rows = ""
    for i, it in enumerate(items):
        y = start_y + i * (step_h + step_gap)
        cx, cy = PAD + 36, y + step_h / 2
        rows += f"""
    <circle cx="{_num(cx)}" cy="{_num(cy)}" r="28" fill="{BRAND["green"]}"/>
    <text x="{_num(cx)}" y="{_num(cy + 8)}" font-family="Inter, system-ui, sans-serif" font-size="22" font-weight="700" fill="{BRAND["white"]}" text-anchor="middle">{i + 1}</text>
    <rect x="{PAD + 88}" y="{_num(y)}" width="{W - PAD * 2 - 88}" height="{_num(step_h)}" fill="{BRAND["cloud"]}" rx="10" ry="10" opacity="0.06"/>
    {text_wrapped(it, x=PAD + 108, y=y + 36, max_chars=78, max_lines=2, line_height=24, fill=BRAND["white"], size=18, weight=500)}"""
    return frame(BRAND["navy"], title_block(inp.title, BRAND["white"]) + rows + footer(True))


def layout_comparison(inp: InfographicInput) -> str:
    left = split_bullets(inp.section_what_changed, 5)
    # `a ?? b`: fall back to do_now only when law_says is null/undefined, not when empty.
    right_src = inp.section_law_says if inp.section_law_says is not None else inp.section_do_now
    right = split_bullets(right_src, 5)
    rows = max(len(left), len(right), 3)
    table_y, table_h, header_h = 200, 330, 50
    col_w = (W - PAD * 2) / 2
    row_h = (table_h - header_h) / rows
    body = f"""
    <rect x="{PAD}"          y="{table_y}" width="{_num(col_w)}" height="{header_h}" fill="{BRAND["navy"]}"  rx="10" ry="10"/>
    <rect x="{_num(PAD + col_w)}"   y="{table_y}" width="{_num(col_w)}" height="{header_h}" fill="{BRAND["green"]}" rx="10" ry="10"/>
    <text x="{PAD + 20}"        y="{table_y + 32}" font-family="Inter, system-ui, sans-serif" font-size="14" font-weight="700" fill="{BRAND["cloud"]}" letter-spacing="2">CLAIM</text>
    <text x="{_num(PAD + col_w + 20)}" y="{table_y + 32}" font-family="Inter, system-ui, sans-serif" font-size="14" font-weight="700" fill="{BRAND["white"]}" letter-spacing="2">VERDICT</text>"""
    for i in range(rows):
        y = table_y + header_h + i * row_h
        stripe = BRAND["cloud"] if i % 2 == 0 else "#EEF2F7"
        lt = left[i] if i < len(left) else ""
        rt = right[i] if i < len(right) else ""
        body += f"""
      <rect x="{PAD}" y="{_num(y)}" width="{W - PAD * 2}" height="{_num(row_h)}" fill="{stripe}"/>
      <line x1="{_num(PAD + col_w)}" y1="{_num(y)}" x2="{_num(PAD + col_w)}" y2="{_num(y + row_h)}" stroke="{BRAND["green"]}" stroke-width="2"/>
      {text_wrapped(lt, x=PAD + 20, y=y + 26, max_chars=44, max_lines=2, line_height=22, fill=BRAND["slate"], size=15, weight=500)}
      {text_wrapped(rt, x=PAD + col_w + 20, y=y + 26, max_chars=44, max_lines=2, line_height=22, fill=BRAND["navy"], size=15, weight=600)}"""
    return frame(BRAND["cloud"], title_block(inp.title, BRAND["navy"]) + body + footer(False))


def layout_checklist(inp: InfographicInput) -> str:
    items = [*split_bullets(inp.section_do_now, 8), *split_bullets(inp.section_what_changed, 8)][:8]
    while len(items) < 4:
        items.append("")
    two_col = len(items) > 4
    col_w = (W - PAD * 2 - 40) / 2 if two_col else W - PAD * 2
    per_col = math.ceil(len(items) / 2) if two_col else len(items)
    start_y, row_h = 200, 56
    rows = ""
    for i, it in enumerate(items):
        second = two_col and i >= per_col
        col = 1 if second else 0
        row = i - per_col if second else i
        x = PAD + col * (col_w + 40)
        y = start_y + row * row_h
        rows += f"""
    <circle cx="{_num(x + 16)}" cy="{y + 14}" r="14" fill="{BRAND["green"]}"/>
    <path d="M {_num(x + 10)} {y + 14} l 4 4 l 8 -8" stroke="{BRAND["white"]}" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
    {text_wrapped(it, x=x + 44, y=y + 18, max_chars=38 if two_col else 80, max_lines=2, line_height=22, fill=BRAND["slate"], size=15, weight=500)}"""
    return frame(BRAND["cloud"], title_block(inp.title, BRAND["navy"]) + rows + footer(False))


def layout_timeline(inp: InfographicInput) -> str:
    items = [*split_bullets(inp.section_what_changed, 5), *split_bullets(inp.section_law_says, 5)][
        :5
    ]
    while len(items) < 3:
        items.append("")
    count, line_y = len(items), 330
    start_x, end_x = PAD + 40, W - PAD - 40
    step_x = (end_x - start_x) / (count - 1) if count > 1 else 0
    dots = ""
    for i, it in enumerate(items):
        cx = start_x + i * step_x
        label_y = line_y - 60 if i % 2 == 0 else line_y + 90
        dots += f"""
    <circle cx="{_num(cx)}" cy="{line_y}" r="14" fill="{BRAND["gold"]}"/>
    <circle cx="{_num(cx)}" cy="{line_y}" r="6"  fill="{BRAND["navy"]}"/>
    {text_wrapped(it, x=cx, y=label_y, max_chars=22, max_lines=3, line_height=20, fill=BRAND["white"], size=14, weight=500, anchor="middle")}"""
    line = (
        f'<line x1="{start_x}" y1="{line_y}" x2="{end_x}" y2="{line_y}" stroke="{BRAND["green"]}" '
        'stroke-width="3" stroke-linecap="round"/>'
    )
    return frame(BRAND["navy"], title_block(inp.title, BRAND["white"]) + line + dots + footer(True))


_LAYOUTS = {
    "stat": layout_stat,
    "process": layout_process,
    "comparison": layout_comparison,
    "checklist": layout_checklist,
    "timeline": layout_timeline,
}


def layout_for(lane: str) -> LayoutId:
    return LANE_LAYOUT.get(lane, DEFAULT_LAYOUT)


def build_svg(inp: InfographicInput) -> str:
    return _LAYOUTS[layout_for(inp.lane)](inp)


# ── rasterise ──────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _font_face_css() -> str:
    faces = []
    for weight, name in ((400, "Inter-Regular.ttf"), (700, "Inter-Bold.ttf")):
        data = base64.b64encode((FONT_DIR / name).read_bytes()).decode()
        faces.append(
            f"@font-face{{font-family:'Inter';font-weight:{weight};"
            f"src:url(data:font/ttf;base64,{data}) format('truetype');}}"
        )
    return "".join(faces)


def rasterize_png(svg: str) -> bytes:
    """SVG → 1200×630 PNG via headless Chromium with the bundled Inter faces."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 — heavy import, only when used

    inline_svg = svg.split("?>", 1)[1] if svg.startswith("<?xml") else svg
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        f"{_font_face_css()}html,body{{margin:0;padding:0;background:transparent}}svg{{display:block}}"
        f"</style></head><body>{inline_svg}</body></html>"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page(viewport={"width": W, "height": H})
            page.set_content(html, wait_until="load")
            page.evaluate("document.fonts.ready")
            return page.screenshot(
                type="png", clip={"x": 0, "y": 0, "width": W, "height": H}, omit_background=True
            )
        finally:
            browser.close()
