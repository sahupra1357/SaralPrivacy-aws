"""Read today's topic from the Google Sheets roadmap (CSV fallback) and mark it published.

Rewritten from tools/read_roadmap.py. A row is picked when its "Plan Published Date"
(e.g. `29-Mar-26`) equals the target date and "Published" is not "Yes". After a successful
publish the row gets `sent=done`, `Published=Yes`, `Actual Publish Date=<d-Mon-yy>`.
"""

import csv
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.editorial import config
from app.editorial.taxonomy import derive

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
MARK_COLUMNS = ("sent", "Published", "Actual Publish Date")


class RoadmapUnavailable(RuntimeError):
    pass


# ── dates ──────────────────────────────────────────────────────────────────
def sheet_date_str(d: date) -> str:
    """'29-Mar-26' — the format of the Plan/Actual Publish Date columns (day not padded)."""
    return f"{d.day}-{d.strftime('%b')}-{d.strftime('%y')}"


def parse_sheet_date(value: str) -> date | None:
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) == 3:
        try:
            return datetime.strptime(
                f"{parts[0].zfill(2)}-{parts[1]}-{parts[2]}", "%d-%b-%y"
            ).date()
        except ValueError:
            pass
    log.warning("Could not parse sheet date: %r", value)
    return None


# ── sources ────────────────────────────────────────────────────────────────
def _open_sheet() -> Any | None:
    """gspread worksheet 1, or None when Sheets is not configured/installed."""
    sheet_id = config.google_sheet_id()
    if not sheet_id:
        return None
    try:
        import gspread  # noqa: PLC0415
        from google.oauth2.service_account import Credentials  # noqa: PLC0415
    except ImportError:
        log.warning("gspread not installed — falling back to local CSV")
        return None

    raw_json = config.google_credentials_json()
    path = config.google_credentials_path()
    if raw_json:
        creds = Credentials.from_service_account_info(json.loads(raw_json), scopes=SCOPES)
    elif path and Path(path).exists():
        creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    else:
        log.warning("Google service-account credentials not configured — falling back to local CSV")
        return None
    return gspread.authorize(creds).open_by_key(sheet_id).sheet1


def sheet_rows() -> list[dict[str, Any]]:
    sheet = _open_sheet()
    if sheet is None:
        return []
    records = sheet.get_all_records()
    log.info("Fetched %s rows from Google Sheets", len(records))
    return list(records)


def csv_rows() -> list[dict[str, Any]]:
    path = config.roadmap_csv_path()
    if not path or not Path(path).exists():
        raise RoadmapUnavailable(f"Roadmap CSV not found: {path or '(ROADMAP_CSV_PATH unset)'}")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_rows() -> list[dict[str, Any]]:
    """Google Sheets first, local CSV as the fallback. Column names are whitespace-stripped."""
    rows = sheet_rows()
    if not rows:
        log.info("Using local CSV roadmap")
        rows = csv_rows()
    return [{(k or "").strip(): v for k, v in r.items()} for r in rows]


# ── selection ──────────────────────────────────────────────────────────────
def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pick(rows: list[dict[str, Any]], target: date) -> dict[str, Any]:
    """The roadmap result for `target` — `{"skip": True, ...}` when nothing is planned."""
    date_str = target.isoformat()
    row = next(
        (
            r
            for r in rows
            if parse_sheet_date(str(r.get("Plan Published Date", ""))) == target
            and str(r.get("Published", "")).strip().lower() != "yes"
        ),
        None,
    )
    if row is None:
        log.info(
            "No planned topic for %s (sheet format: %s) — skipping",
            date_str,
            sheet_date_str(target),
        )
        return {"skip": True, "reason": "no_planned_topic_today", "date": date_str}

    facets = derive(row)
    return {
        "skip": False,
        "date": date_str,
        "day": _int(row.get("day", 1), 1),
        "week": _int(row.get("week", 1), 1),
        "week_theme": row.get("week_theme", ""),
        "topic": row.get("topic", ""),
        "concept": row.get("concept", ""),
        "clarification": row.get("clarification", ""),
        "save_worthy_takeaway": row.get("save_worthy_takeaway", ""),
        "publish_channels": [
            c.strip() for c in str(row.get("publish_channels", "email")).split("|")
        ],
        "infographic_type": row.get("infographic_type", "stat"),
        "research_query": row.get("research_query", row.get("topic", "")),
        "stage": facets["stage"],
        "sector": facets["sector"],
        "content_type": facets["content_type"],
        "total_days": len(rows),
    }


def read_roadmap(target: date) -> dict[str, Any]:
    return pick(load_rows(), target)


# ── mark published ─────────────────────────────────────────────────────────
def mark_in_sheet(day_number: int, actual: str, status: str = "done") -> bool:
    try:
        sheet = _open_sheet()
        if sheet is None:
            return False
        import gspread.utils  # noqa: PLC0415

        records = sheet.get_all_records()
        col_idx = {h.strip(): i + 1 for i, h in enumerate(sheet.row_values(1))}
        values = {"sent": status, "Published": "Yes", "Actual Publish Date": actual}
        for i, record in enumerate(records):
            if _int(record.get("day", 0), -1) != day_number:
                continue
            row_index = i + 2  # header row + 1-based
            updates = [
                {
                    "range": gspread.utils.rowcol_to_a1(row_index, col_idx[name]),
                    "values": [[values[name]]],
                }
                for name in MARK_COLUMNS
                if name in col_idx
            ]
            if updates:
                sheet.batch_update(updates)
            log.info(
                "Day %s marked Published=Yes, Actual Publish Date=%s in Google Sheet",
                day_number,
                actual,
            )
            return True
        log.warning("Day %s not found in Google Sheet", day_number)
        return False
    except Exception as e:  # noqa: BLE001 — marking never fails the publish
        log.error("Google Sheets update failed: %s", e)
        return False


def mark_in_csv(day_number: int, actual: str, status: str = "done") -> bool:
    path = config.roadmap_csv_path()
    if not path or not Path(path).exists():
        log.warning("CSV not found at %r — cannot mark day %s", path, day_number)
        return False
    try:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
        for name in MARK_COLUMNS:
            if name not in fieldnames:
                fieldnames.append(name)
        for row in rows:
            if _int(row.get("day", 0), -1) == day_number:
                row.update({"sent": status, "Published": "Yes", "Actual Publish Date": actual})
                break
        else:
            log.warning("Day %s not found in CSV — nothing marked", day_number)
            return False
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("Failed to mark day %s in CSV: %s", day_number, e)
        return False


def mark_published(day_number: int, today: date) -> bool:
    actual = sheet_date_str(today)
    marked = mark_in_sheet(day_number, actual) if config.google_sheet_id() else False
    return marked or mark_in_csv(day_number, actual)
