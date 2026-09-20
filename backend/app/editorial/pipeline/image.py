"""Briefing infographic via KIE.ai Nano Banana (rewritten from tools/generate_infographic.py).

Async job API: POST createTask → poll recordInfo until `success` → download the image →
stamp the `© saralprivacy.com` watermark → JPEG bytes. When KIE.ai is unavailable the
briefing publishes without an image (the old SVG fallback was written to .tmp but never
uploaded, because publish only looked for jpg/jpeg/png).
"""

import io
import json
import logging
import time
from typing import Any

import httpx

from app.editorial import config

log = logging.getLogger(__name__)

KIE_API_BASE = "https://api.kie.ai/api/v1"
KIE_CREATE_TASK = f"{KIE_API_BASE}/jobs/createTask"
KIE_RECORD_INFO = f"{KIE_API_BASE}/jobs/recordInfo"

BRAND_NAVY = "#1B3A5C"
BRAND_SAFFRON = "#F4941B"
BRAND_WHITE = "#FFFFFF"

MAX_RETRIES = 2
RETRY_DELAY = 8
POLL_INTERVAL = 5
POLL_TIMEOUT = 120

_sleep = time.sleep  # patched in tests
_monotonic = time.monotonic

INFOGRAPHIC_STYLE = (
    f"Professional infographic design. Clean, modern layout. "
    f"Color palette: navy blue {BRAND_NAVY} as primary, saffron orange {BRAND_SAFFRON} as accent, "
    f"white {BRAND_WHITE} background. Sans-serif typography. "
    f"Indian business context. No watermarks. No borders. Flat design style. "
    f"Newsletter-ready, 600px wide format."
)

INFOGRAPHIC_TYPE_INSTRUCTIONS = {
    "stat": (
        "Large statistic callout cards. 2-3 bold numbers or key facts displayed prominently. "
        "Each card has a number/value, short label, and accent color bar. "
        "Horizontal layout. Clean white cards with navy headings and saffron highlights."
    ),
    "process": (
        "Vertical step-by-step process flowchart. "
        "Numbered steps with connecting arrows. "
        "Each step has an icon area, bold heading, and short description. "
        "Navy numbered circles, saffron arrows, white card backgrounds."
    ),
    "timeline": (
        "Horizontal timeline. "
        "Nodes connected by a line showing progression. "
        "Each node has a year/label and short event description. "
        "Navy line, saffron milestone dots, white background."
    ),
    "checklist": (
        "Visual checklist or summary card. "
        "Bullet points with checkmark icons. "
        "Clear heading at top, items below with icons. "
        "Two-column layout if more than 5 items. "
        "Navy headings, saffron checkmarks."
    ),
    "comparison": (
        "Side-by-side comparison table. "
        "Two columns with a clear divider. "
        "Row-by-row comparison of attributes. "
        "Navy headers, alternating white/light rows, saffron highlights for key differences."
    ),
}


class KieError(RuntimeError):
    pass


def build_prompt(content: dict[str, Any]) -> str:
    inf = content.get("infographic", {})
    inf_type = inf.get("type", "stat")
    inf_title = inf.get("title", content.get("topic", ""))
    data_points = inf.get("data_points", [])
    type_instruction = INFOGRAPHIC_TYPE_INSTRUCTIONS.get(
        inf_type, INFOGRAPHIC_TYPE_INSTRUCTIONS["stat"]
    )
    data_block = (
        "Data to visualise:\n" + "\n".join(f"- {dp}" for dp in data_points) if data_points else ""
    )

    return f"""Create a professional newsletter infographic.

Title: "{inf_title}"
Topic: {content.get("topic", "")} (DPDPA Daily Brief — Day {content.get("day_number", 1)})

Layout type: {type_instruction}

{inf.get("description", "")}

{data_block}

Style: {INFOGRAPHIC_STYLE}

The infographic must be self-contained and readable without additional context.
Include the title prominently at the top.
Label: "© saralprivacy.com" in small text at the bottom left corner. Keep the bottom right corner clear — do not place any text or watermark there.
Image dimensions: approximately 560px wide x 280px tall (landscape, 2:1 ratio)."""


def call_kie(prompt: str, api_key: str) -> bytes:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": config.nano_banana_model(),
        "input": {
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "resolution": "1K",
            "output_format": "jpg",
            "google_search": False,
            "image_input": [],
        },
    }
    resp = httpx.post(KIE_CREATE_TASK, headers=headers, json=payload, timeout=30)
    if resp.status_code == 401:
        raise KieError("KIE.ai API key invalid or expired. Check KIE_API_KEY in .env")
    if resp.status_code == 429:
        raise KieError("KIE.ai rate limit hit. Wait and retry.")
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise KieError(f"KIE.ai createTask failed: {data.get('msg', data)}")
    task_id = data["data"]["taskId"]
    log.info("KIE.ai task submitted: %s", task_id)

    deadline = _monotonic() + POLL_TIMEOUT
    while _monotonic() < deadline:
        _sleep(POLL_INTERVAL)
        poll = httpx.get(KIE_RECORD_INFO, headers=headers, params={"taskId": task_id}, timeout=15)
        poll.raise_for_status()
        pdata = poll.json()
        if pdata.get("code") != 200:
            raise KieError(f"KIE.ai recordInfo error: {pdata}")
        task = pdata.get("data", {}) or {}
        state = task.get("state", "")
        if state == "success":
            urls = json.loads(task.get("resultJson") or "{}").get("resultUrls", [])
            if not urls:
                raise KieError("KIE.ai task succeeded but no result URL")
            img = httpx.get(urls[0], timeout=30)
            img.raise_for_status()
            return img.content
        if state in ("failed", "error"):
            raise KieError(f"KIE.ai task failed: {task.get('failMsg', 'unknown error')}")
    raise KieError(f"KIE.ai task {task_id} did not complete within {POLL_TIMEOUT}s")


def add_watermark(image_bytes: bytes) -> bytes:
    """Stamp '© saralprivacy.com' bottom-right (navy pill, saffron text). Returns the
    original bytes if Pillow cannot process the image."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        text = "© saralprivacy.com"
        font_size = max(12, img.width // 40)
        font: Any
        try:
            from app.editorial.infographic import FONT_DIR  # noqa: PLC0415

            font = ImageFont.truetype(str(FONT_DIR / "Inter-Bold.ttf"), font_size)
        except OSError:
            font = ImageFont.load_default()
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        tw, th = x1 - x0, y1 - y0
        margin = 10
        x, y = img.width - tw - margin, img.height - th - margin
        draw.rounded_rectangle(
            [x - 6, y - 4, x + tw + 6, y + th + 4], radius=4, fill=(27, 58, 92, 180)
        )
        draw.text((x, y), text, font=font, fill=(244, 148, 27, 220))
        out = io.BytesIO()
        Image.alpha_composite(img, overlay).convert("RGB").save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as e:  # noqa: BLE001
        log.warning("Watermark failed (Pillow issue): %s — returning original image", e)
        return image_bytes


def generate_image(content: dict[str, Any]) -> bytes | None:
    """JPEG bytes, or None when KIE.ai is not configured or every attempt failed."""
    api_key = config.kie_api_key()
    if not api_key:
        log.warning("KIE_API_KEY not set — publishing without an infographic")
        return None
    prompt = build_prompt(content)
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            raw = call_kie(prompt, api_key)
            log.info("KIE.ai infographic generated (%s bytes)", len(raw))
            return add_watermark(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("KIE.ai attempt %s failed: %s", attempt, e)
            if attempt <= MAX_RETRIES:
                _sleep(RETRY_DELAY)
    log.error("KIE.ai failed — publishing without an infographic")
    return None
