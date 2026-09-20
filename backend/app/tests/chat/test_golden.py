"""Golden routing set — the acceptance gate the TypeScript suite enforced
(frontend/lib/chat/golden.test.ts, cases in frontend/eval/chat-golden.json, copied to
app/data/chat-golden.json).

Offline and deterministic: the lexical path only, exactly as the original gate ran.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chat.journeys import create_initial_state
from app.services.chat.orchestrate import build_meta, plan_turn

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[2] / "data" / "chat-golden.json").read_text(encoding="utf-8")
)
CORE = [c for c in GOLDEN["cases"] if not c.get("stretch")]


def run_case(case: dict[str, Any]) -> tuple[bool, str]:
    plan = plan_turn(case["query"], create_initial_state("golden", "/"))
    meta = build_meta(plan)
    urls = [c.url for c in meta.citations] + [a.url for a in meta.actions]

    if case.get("expectRefusal"):
        return meta.refusal, "refused" if meta.refusal else f"answered with {', '.join(urls)}"
    if meta.refusal:
        return (
            case.get("allowRefusal") is True and "/contact" in urls,
            f"refused (allowRefusal={case.get('allowRefusal', False)})",
        )
    hit = next((u for u in case["accept"] if u in urls), None)
    return hit is not None, f"hit {hit}" if hit else f"got {', '.join(urls[:4])}"


def test_golden_core_routing_accuracy_meets_the_gate() -> None:
    assert len(CORE) >= 40, f"only {len(CORE)} core cases"
    failures = [(c, detail) for c in CORE for ok, detail in [run_case(c)] if not ok]
    rate = (len(CORE) - len(failures)) / len(CORE)
    report = "\n".join(f'  FAIL [{c["id"]}] "{c["query"]}" -> {d}' for c, d in failures)
    assert rate >= GOLDEN["gate"], f"routing accuracy {rate:.1%} below gate\n{report}"


@pytest.mark.parametrize(
    "case",
    [c for c in CORE if c.get("expectRefusal")],
    ids=lambda c: c["id"],
)
def test_refusal_cases_are_a_hard_zero_hallucination_gate(case: dict[str, Any]) -> None:
    ok, detail = run_case(case)
    assert ok, f"[{case['id']}] must refuse but {detail}"
