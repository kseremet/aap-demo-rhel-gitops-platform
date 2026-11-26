"""Schedule bounded attempts for reviewer gaps; never resolve or erase findings."""

import copy


def prepare_review(review):
    if not isinstance(review, dict):
        raise ValueError("Reviewer handoff must be an object")
    result = copy.deepcopy(review)
    findings = result.get("blocking_findings")
    checks = result.get("follow_up")
    unavailable = result.get("unobtainable_findings", [])
    if not isinstance(findings, list) or any(
        not isinstance(f, str) or not f.strip() for f in findings
    ):
        raise ValueError("Reviewer findings must be nonempty strings")
    if (
        not isinstance(checks, list)
        or len(checks) > 4
        or not isinstance(unavailable, list)
    ):
        raise ValueError("Reviewer checks must be a list of at most four items")
    exempt = set()
    for item in unavailable:
        if (
            not isinstance(item, dict)
            or item.get("finding") not in findings
            or not isinstance(item.get("reason"), str)
            or not item["reason"].strip()
        ):
            raise ValueError("Unavailable findings require an exact finding and reason")
        # Budget resets per activity. A spent collector budget cannot make an
        # obtainable observation unavailable to a new follow-up activity.
        if any(
            word in item["reason"].lower()
            for word in ("budget", "four-call", "four call", "call limit")
        ):
            continue
        exempt.add(item["finding"])
    covered = set()
    for check in checks:
        if not isinstance(check, dict) or any(
            not isinstance(check.get(k), str) or not check[k].strip()
            for k in ("question", "reason", "success_criterion")
        ):
            raise ValueError(
                "Each check requires question, reason and success criterion"
            )
        finding = check.get("finding")
        if finding is not None and finding not in findings:
            raise ValueError("A check must reference an exact reviewer finding")
        if finding:
            covered.add(finding)
    for finding in findings:
        if finding in covered or finding in exempt or len(checks) == 4:
            continue
        checks.append(
            {
                "finding": finding,
                "question": "Attempt to resolve this exact finding using the selected read-only tools: "
                + finding,
                "reason": "The reviewer left this finding unanswered. This new activity has its own four-call budget.",
                "success_criterion": "Return exact source-attributed observations that resolve the finding, or retain it with the concrete access/authority limitation.",
            }
        )
        covered.add(finding)
    # More than four gaps stay blocking; the scheduler never invents a resolution.
    result["follow_up"] = checks
    return result
