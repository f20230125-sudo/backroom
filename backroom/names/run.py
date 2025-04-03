"""Replay grey-band checks offline; --record explicitly enables the senior's recorder."""

import argparse
from collections import Counter

from backroom import DATA
from backroom.db import connect, migrate
from backroom.db.repo import dumps
from backroom.inputs import manifest, offline_llm
from backroom.names.match import Matcher, approved_names, masters, propose
from backroom.pipeline import load_masters
from backroom.settings import build_llm
from eval.m3 import name_metrics


def run_names(*, repo, source, llm=None, record=False):
    """Record/replay proposals without applying any grey-band merge or approval."""
    owned = llm is None
    llm = llm if llm is not None else (build_llm(offline=False) if record else offline_llm())
    try:
        migrate(repo)
        load_masters(repo, source)
        matcher = Matcher(masters(source), approved_names(repo))
        observed = {
            (r["entity_type"], r["variant"])
            for r in repo.rows("SELECT entity_type, variant FROM name_map")
        }
        observed.update((r["entity_type"], r["variant"]) for r in source.get("name_variants", []))
        counts = Counter()
        for kind, name in sorted(observed):
            proposal = matcher.match(kind, name, llm=llm, model_check=True)
            propose(repo, proposal)
            counts[proposal.decision] += 1
        # Include both hard-negative directions in the recorder, with the same
        # requests the offline evaluator will replay. Answer labels never enter prompts.
        scores = name_metrics(source, llm=llm)
        repo.commit()
        return {
            "mode": "record" if record else "offline",
            "proposals": dict(counts),
            "names": scores,
        }
    finally:
        if owned:
            if record:
                llm.store.db.close()
            else:
                llm.store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Default: cache replay only")
    mode.add_argument(
        "--record", action="store_true", help="Record model checks using the configured key"
    )
    args = parser.parse_args()
    try:
        with connect() as repo:
            result = run_names(repo=repo, source=manifest(DATA), record=args.record)
    except Exception as exc:
        parser.exit(1, f"Name checks failed ({type(exc).__name__}); details withheld.\n")
    print(dumps(result))


if __name__ == "__main__":
    main()
