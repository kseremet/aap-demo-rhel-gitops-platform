"""Bounded follow-up scheduling preserves blockers and never fabricates facts."""

import runpy
from pathlib import Path
import pytest

prepare = runpy.run_path(str(Path(__file__).parents[1] / "files/ao/review_checks.py"))[
    "prepare_review"
]


def review(count=3):
    return {
        "summary": "Missing evidence",
        "blocking_findings": [f"Gap {i}" for i in range(count)],
        "follow_up": [],
        "unobtainable_findings": [],
    }


def test_empty_checks_get_concrete_bounded_attempts():
    original = review()
    out = prepare(original)
    assert len(out["follow_up"]) == 3
    assert out["blocking_findings"] == original["blocking_findings"]
    assert original["follow_up"] == []
    assert [c["finding"] for c in out["follow_up"]] == original["blocking_findings"]


def test_explicit_unavailable_findings_remain_blocking():
    r = review()
    r["unobtainable_findings"] = [
        {
            "finding": "Gap 0",
            "reason": "Requires policy-owner approval; no repository policy exists.",
        }
    ]
    out = prepare(r)
    assert len(out["follow_up"]) == 2
    assert "Gap 0" in out["blocking_findings"]


def test_previous_activity_budget_cannot_disable_followups():
    r = review(1)
    r["unobtainable_findings"] = [
        {"finding": "Gap 0", "reason": "The prior four-call budget was exhausted."}
    ]
    assert len(prepare(r)["follow_up"]) == 1


def test_four_check_bound_and_all_findings_retained():
    out = prepare(review(6))
    assert len(out["follow_up"]) == 4
    assert len(out["blocking_findings"]) == 6
    assert prepare(out) == out


def test_malformed_review_is_not_silently_accepted():
    r = review()
    r["follow_up"] = [{"question": "unscoped"}]
    with pytest.raises(ValueError):
        prepare(r)
