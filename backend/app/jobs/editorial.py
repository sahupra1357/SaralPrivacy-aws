"""Editorial jobs: the 09:00 IST daily briefing pipeline and the taxonomy backfill.

Replaces run_pipeline.sh + .github/workflows/daily-briefing.yml (cron `30 3 * * *`) and
.github/workflows/backfill-taxonomy.yml (manual). Registered by the orchestrator as

    register("editorial_daily_briefing", "30 3 * * *", run_daily_briefing, timeout_seconds=900)

Manual runs (were `workflow_dispatch` inputs):

    python -m app.jobs.editorial pipeline [--date YYYY-MM-DD]
    python -m app.jobs.editorial backfill-taxonomy [--apply] [--revert FILE]
"""

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.crud import editorial as crud
from app.editorial.briefings import ist_today
from app.editorial.docs import js_json, parse_json
from app.editorial.pipeline import content as content_step
from app.editorial.pipeline import image as image_step
from app.editorial.pipeline import publish as publish_step
from app.editorial.pipeline import research as research_step
from app.editorial.pipeline import roadmap
from app.editorial.taxonomy import derive
from app.jobs.registry import JobResult
from app.services import revalidate

log = logging.getLogger(__name__)

JOB_NAME = "editorial_daily_briefing"
CRON = "30 3 * * *"  # 09:00 IST


# ── daily briefing ─────────────────────────────────────────────────────────
def run_pipeline(session: Session, target: date) -> JobResult:
    date_str = target.isoformat()
    log.info("DPDPA PIPELINE START for %s", date_str)

    try:
        plan = roadmap.read_roadmap(target)
    except roadmap.RoadmapUnavailable as e:
        return JobResult(ok=False, summary=str(e))
    if plan.get("skip"):
        return JobResult(
            ok=True, summary=f"No topic planned for {date_str} ({plan.get('reason')}) — skipped"
        )

    # Double-run guard on top of the Sheet flag: the day is already live.
    if crud.published_briefing_for_date_exists(session, date_str):
        return JobResult(ok=True, summary=f"Briefing for {date_str} already published — skipped")

    log.info("Day %s: %s", plan.get("day"), plan.get("topic"))
    found = research_step.research(plan)
    content = content_step.generate_content(found)

    # The content model drops week_theme and the explicit facets; merge them back
    # from the roadmap without overwriting anything the content provides.
    for key in ("week_theme", "stage", "sector", "content_type", "infographic_type"):
        if not content.get(key) and plan.get(key):
            content[key] = plan[key]

    image = image_step.generate_image(content)
    briefing = publish_step.publish(session, content, image)

    day_number = int(content.get("day_number") or plan.get("day") or 1)
    if not roadmap.mark_published(day_number, ist_today()):
        log.warning("Could not mark Day %s as done in the roadmap", day_number)

    return JobResult(
        ok=True,
        summary=f"Day {day_number} published: {briefing.slug}",
        details={
            "date": date_str,
            "day_number": day_number,
            "topic": content.get("topic", ""),
            "briefing_id": str(briefing.id),
            "slug": briefing.slug,
            "status": "published",
            "has_infographic": bool(briefing.infographic_base64),
        },
    )


def run_daily_briefing(session: Session) -> JobResult:
    return run_pipeline(session, ist_today())


# ── taxonomy backfill ──────────────────────────────────────────────────────
def build_slug_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for r in rows:
        slug = str(r.get("slug", "")).strip()
        if slug:
            index[slug] = {
                "week_theme": r.get("week_theme", ""),
                "topic": r.get("topic", ""),
                "infographic_type": r.get("infographic_type", "stat"),
            }
    return index


def plan_changes(
    briefings: list[Any], slug_index: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for b in briefings:
        row = slug_index.get(b.slug)
        if not row:
            unmatched.append({"id": str(b.id), "slug": b.slug, "title": b.title})
            continue
        tax = derive(row)
        old_tags = parse_json(b.tags, [])
        old_tags = old_tags if isinstance(old_tags, list) else []
        old_ind = parse_json(b.industries, [])
        new = {
            "category": tax["stage"],
            "industries": js_json([tax["sector"]]),
            "tags": js_json(
                [tax["content_type"]] + [t for t in old_tags if t != tax["content_type"]]
            ),
        }
        old = {
            "category": b.category or "",
            "industries": js_json(old_ind if isinstance(old_ind, list) else []),
            "tags": js_json(old_tags),
        }
        if new != old:
            changes.append(
                {
                    "id": str(b.id),
                    "slug": b.slug,
                    "title": b.title,
                    "old": old,
                    "new": new,
                    "facets": tax,
                }
            )
    return changes, unmatched


def backfill_taxonomy(
    session: Session, *, apply: bool, backup_dir: Path | None = None
) -> dict[str, Any]:
    briefings = crud.list_all_briefings(session)
    changes, unmatched = plan_changes(briefings, build_slug_index(roadmap.load_rows()))
    report: dict[str, Any] = {
        "total": len(briefings),
        "changes": len(changes),
        "unmatched": len(unmatched),
        "stages": dict(Counter(c["facets"]["stage"] for c in changes)),
        "sample": [
            f"{c['slug'][:48]:<48} {c['old']['category']:>20} -> "
            f"{c['facets']['stage']}/{c['facets']['sector']}/{c['facets']['content_type']}"
            for c in changes[:8]
        ],
        "applied": 0,
        "backup": None,
    }
    if not apply:
        return report

    backup_dir = backup_dir or Path.cwd()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"backfill_backup_{int(time.time())}.json"
    backup.write_text(
        json.dumps([{"id": c["id"], "data": c["old"]} for c in changes], indent=2), encoding="utf-8"
    )
    report["backup"] = str(backup)

    for c in changes:
        row = crud.get_briefing(session, c["id"])
        if row is None:
            continue
        crud.update_briefing(session, row, **c["new"])
        report["applied"] += 1
    if report["applied"]:
        revalidate.tag("briefings")
    return report


def revert_taxonomy(session: Session, backup_file: Path) -> int:
    entries = json.loads(backup_file.read_text(encoding="utf-8"))
    restored = 0
    for entry in entries:
        row = crud.get_briefing(session, entry["id"])
        if row is not None:
            crud.update_briefing(session, row, **entry["data"])
            restored += 1
    if restored:
        revalidate.tag("briefings")
    return restored


# ── CLI ────────────────────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    from app.core.db import engine  # noqa: PLC0415 — only for the CLI
    from app.jobs.registry import JobSpec  # noqa: PLC0415
    from app.jobs.runner import run_job  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="python -m app.jobs.editorial")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pipeline", help="run the daily briefing for a date (default: today IST)")
    p.add_argument("--date", default=None)
    b = sub.add_parser("backfill-taxonomy", help="reclassify briefing facets (dry-run by default)")
    b.add_argument("--apply", action="store_true")
    b.add_argument("--revert", metavar="FILE")
    args = parser.parse_args(argv)

    if args.cmd == "pipeline":
        target = date.fromisoformat(args.date) if args.date else ist_today()
        # Same advisory lock as the scheduled run, so a backfill never races the cron.
        result = run_job(JobSpec(name=JOB_NAME, cron=CRON, fn=lambda s: run_pipeline(s, target)))
        print(
            json.dumps(
                {
                    "ok": bool(result and result.ok),
                    "summary": result.summary if result else "locked",
                }
            )
        )
        return 0 if result and result.ok else 1

    with Session(engine) as session:
        if args.revert:
            print(
                f"Reverted {revert_taxonomy(session, Path(args.revert))} briefings from {args.revert}"
            )
            return 0
        report = backfill_taxonomy(session, apply=args.apply)
        print(json.dumps(report, indent=2))
        if not args.apply:
            print("DRY-RUN — nothing written. Re-run with --apply to patch the briefings table.")
        return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main(sys.argv[1:]))
