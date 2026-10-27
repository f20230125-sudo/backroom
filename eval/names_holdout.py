"""Held-out name matching: variants and traps written by the reviewer after the matcher was built.

The 40 variants in the ground truth were visible while the matcher was developed, so their score
is a development score. These were not, so this is the honest test of how the rules generalise.
"""

import json
from pathlib import Path

from backroom.names.match import Matcher, masters

HOLDOUT = Path(__file__).with_name("names_holdout.json")


def evaluate_names_holdout(manifest: dict) -> dict:
    data = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    matcher = Matcher(masters(manifest))
    rows, correct, wrong, review_ok = [], 0, 0, 0
    for entity_type, variant, expected in data["variants"]:
        p = matcher.match(entity_type, variant)
        if p.decision == "auto_match":
            outcome = "correct" if p.canonical_id == expected else "wrong_merge"
        else:
            outcome = "review" if p.decision == "review" else "new"
        correct += outcome == "correct"
        wrong += outcome == "wrong_merge"
        review_ok += outcome == "review" and getattr(p, "suggested_id", None) == expected
        rows.append(
            {
                "type": entity_type,
                "variant": variant,
                "expected": expected,
                "decision": p.decision,
                "predicted": p.canonical_id,
                "suggested": getattr(p, "suggested_id", None),
                "outcome": outcome,
            }
        )
    traps = []
    for entity_type, variant in data["traps"]:
        p = matcher.match(entity_type, variant)
        traps.append(
            {
                "type": entity_type,
                "variant": variant,
                "decision": p.decision,
                "merged_into": p.canonical_id if p.decision == "auto_match" else None,
            }
        )
    n = len(data["variants"])
    return {
        "variants": n,
        "auto_matched_correctly": correct,
        "wrong_merges": wrong,
        "left_for_review": n - correct - wrong,
        "review_suggestion_correct": review_ok,
        "traps": len(traps),
        "traps_merged": sum(t["merged_into"] is not None for t in traps),
        "rows": rows,
        "trap_rows": traps,
    }
