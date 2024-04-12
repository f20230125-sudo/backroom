"""Playwright starts a production-mode Flask instance over a fresh offline snapshot."""

import os
import sys

# Playwright runs from web/; keep all files and imports in the shared repository.
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backroom.api import create_app  # noqa: E402
from backroom.db import connect  # noqa: E402
from backroom.pipeline import run_pipeline  # noqa: E402


def main():
    database = ROOT / f".local/codex-e2e-{os.getpid()}.sqlite"
    with connect(database) as repo:
        summary = run_pipeline(repo=repo, offline=True)
    print(
        f"Offline e2e snapshot: {summary['documents']} documents; "
        f"{summary['model_calls']} model calls",
        flush=True,
    )
    app = create_app(
        {
            "DATABASE": database,
            "DATABASE_URL": "",
            "APP_ENV": "production",
            "TRUSTED_HOSTS": ["127.0.0.1", "localhost"],
        }
    )
    port = int(os.environ.get("BACKROOM_E2E_PORT", "5081"))
    # Save only this fixture's PID: restricted Windows cannot kill process trees.
    (ROOT / f".local/codex-e2e-{port}.pid").write_text(str(os.getpid()), encoding="ascii")
    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
