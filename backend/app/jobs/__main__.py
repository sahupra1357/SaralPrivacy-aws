"""Worker entrypoint: `python -m app.jobs [run <name>]`."""

import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import app.jobs  # noqa: F401  (registrations)
from app.jobs.registry import registry
from app.jobs.runner import run_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "run":
        spec = registry.get(argv[1])
        if spec is None:
            log.error("unknown job %r; known: %s", argv[1], sorted(registry))
            return 2
        result = run_job(spec)
        return 0 if result and result.ok else 1

    sched = BlockingScheduler(timezone="UTC")
    for spec in registry.values():
        sched.add_job(
            run_job,
            CronTrigger.from_crontab(spec.cron, timezone="UTC"),
            args=[spec],
            id=spec.name,
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        log.info("scheduled %s @ %s", spec.name, spec.cron)
    if not registry:
        log.warning("no jobs registered; worker idle")
    sched.start()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
