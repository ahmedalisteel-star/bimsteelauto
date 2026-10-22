"""The ~70 module invariant tests, folded into pytest.

Design note — why these are collected rather than rewritten
-----------------------------------------------------------
Each module already owns a ``_run_invariants()`` returning
``[(name, ok, detail), ...]``.  Rewriting those 70 checks as pytest
functions would fork them: the ``__main__`` block and the suite would
drift, and a check added to one would silently not exist in the other.

So the module stays the single source of truth and pytest *collects* from
it, one test case per named check, with the check's own detail string
surfaced on failure.  Adding a ``chk(...)`` line to a module adds a test
here automatically.

The one thing this cannot do is catch a wrong formula — every check here
is relative (twice the thickness, four times the moment).  All seven
defects in HANDOVER section 4 passed every one of them.  That is what
``test_verification_fixtures.py`` and ``test_defect_regressions.py`` are
for; this file guards against coding errors, not transcription errors.
"""

from __future__ import annotations

import importlib

import pytest

MODULES = ("endplate", "welds", "baseplate", "anchors", "secondary")


def _collect() -> list[tuple[str, str, bool, str]]:
    """Run every module's invariants once, at collection time."""
    out: list[tuple[str, str, bool, str]] = []
    for name in MODULES:
        mod = importlib.import_module(f"bimsteelauto_connections.{name}")
        runner = getattr(mod, "_run_invariants", None)
        if runner is None:                     # pragma: no cover
            out.append((name, "<module has no _run_invariants>", False, ""))
            continue
        for check_name, ok, detail in runner():
            out.append((name, check_name, ok, detail))
    return out


_RESULTS = _collect()


@pytest.mark.parametrize(
    "module,check,ok,detail",
    _RESULTS,
    ids=[f"{m}::{c}" for m, c, _, _ in _RESULTS],
)
def test_module_invariant(module: str, check: str, ok: bool, detail: str) -> None:
    assert ok, f"{module}._run_invariants(): {check} — {detail}"


def test_every_module_contributes_invariants() -> None:
    """A module losing its invariants should fail loudly, not quietly."""
    seen = {m for m, _, _, _ in _RESULTS}
    assert seen == set(MODULES), f"missing invariants from {set(MODULES) - seen}"


def test_invariant_count_has_not_shrunk() -> None:
    """Guard against invariants being deleted rather than fixed.

    Counted 2026-08-30 at package version 0.1.0.  Raise the floor when
    you add checks; never lower it to make a run go green.
    """
    FLOOR = {"endplate": 10, "welds": 10, "baseplate": 15,
             "anchors": 21, "secondary": 15}   # 71 total
    actual: dict[str, int] = {}
    for m, _, _, _ in _RESULTS:
        actual[m] = actual.get(m, 0) + 1
    for mod, floor in FLOOR.items():
        assert actual.get(mod, 0) >= floor, (
            f"{mod} now has {actual.get(mod, 0)} invariants, was {floor}. "
            f"Checks were removed — restore them or justify the floor change."
        )
