"""HTML → PDF with Playwright Chromium (replaces puppeteer-core + @sparticuz/chromium)."""

from typing import Any, cast

from playwright.sync_api import sync_playwright

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


_DEFAULT_MARGINS = {"top": "20mm", "bottom": "20mm", "left": "16mm", "right": "16mm"}


def render_html(
    html: str,
    *,
    header_html: str | None = None,
    footer_html: str | None = None,
    format: str = "A4",
    margins: dict[str, str] | None = None,
) -> bytes:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            return page.pdf(
                format=format,
                print_background=True,
                display_header_footer=bool(header_html or footer_html),
                header_template=header_html or "<span></span>",
                footer_template=footer_html or "<span></span>",
                # Playwright types this as its PdfMargins TypedDict; a plain dict of
                # CSS lengths is what it accepts at runtime.
                margin=cast(Any, margins or _DEFAULT_MARGINS),
            )
        finally:
            browser.close()
