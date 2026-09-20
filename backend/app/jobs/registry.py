from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session


@dataclass(frozen=True)
class JobResult:
    ok: bool
    summary: str
    details: dict[str, Any] | None = None


JobFn = Callable[[Session], JobResult]


@dataclass(frozen=True)
class JobSpec:
    name: str
    cron: str  # 5-field cron, UTC (matches the old vercel.json schedules)
    fn: JobFn
    timeout_seconds: int = 300


registry: dict[str, JobSpec] = {}


def register(name: str, cron: str, fn: JobFn, timeout_seconds: int = 300) -> None:
    if name in registry:
        raise ValueError(f"job {name!r} registered twice")
    registry[name] = JobSpec(name=name, cron=cron, fn=fn, timeout_seconds=timeout_seconds)
