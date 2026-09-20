from sqlmodel import Session

# `app.jobs` re-exports the `registry` dict, which shadows the submodule name, so
# import the dict directly rather than the module.
from app.jobs.registry import JobResult, register
from app.jobs.registry import registry as REGISTRY
from app.jobs.runner import run_job


def test_register_rejects_duplicates() -> None:
    REGISTRY.pop("t_dup", None)
    register("t_dup", "0 4 * * *", lambda s: JobResult(True, "ok"))  # noqa: ARG005
    try:
        register("t_dup", "0 4 * * *", lambda s: JobResult(True, "ok"))  # noqa: ARG005
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    finally:
        REGISTRY.pop("t_dup", None)


def test_run_job_returns_result_and_survives_crash(session: Session) -> None:  # noqa: ARG001
    REGISTRY.pop("t_ok", None)
    register("t_ok", "0 4 * * *", lambda s: JobResult(True, "done"))  # noqa: ARG005
    assert run_job(REGISTRY["t_ok"]).summary == "done"  # type: ignore[union-attr]

    def boom(s: Session) -> JobResult:  # noqa: ARG001
        raise RuntimeError("x")

    REGISTRY.pop("t_boom", None)
    register("t_boom", "0 4 * * *", boom)
    res = run_job(REGISTRY["t_boom"])
    assert res is not None and not res.ok
    REGISTRY.pop("t_ok", None)
    REGISTRY.pop("t_boom", None)
