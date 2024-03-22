"""Record/replay every gold question; only explicit --record enables the model provider."""

import argparse
from pathlib import Path

from backroom.ask.agent import ask
from backroom.ask.gold import build_gold
from backroom.db import connect, migrate
from backroom.db.repo import dumps
from backroom.inputs import offline_llm
from backroom.settings import build_llm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    llm = build_llm(offline=False) if args.record else offline_llm()
    try:
        with connect(args.database) as repo:
            migrate(repo)
            gold = build_gold(repo)
            for case in gold["questions"]:
                result = {"question": case["question"], **ask(repo, case["question"], llm=llm)}
                repo.upsert("ask_log", {"id": case["id"], "payload": dumps(result)})
                repo.commit()
                print(
                    dumps(
                        {
                            "id": case["id"],
                            "refused": result["refused"],
                            "not_run": result["not_run"],
                        }
                    )
                )
    finally:
        llm.store.close() if hasattr(llm.store, "close") else llm.store.db.close()


if __name__ == "__main__":
    main()
