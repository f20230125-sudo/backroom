"""Apply migrations without exposing connection credentials."""

import argparse

from backroom.db import connect, migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()
    try:
        with connect() as repo:
            migrate(repo)
    except Exception:
        parser.exit(1, "Database migration failed; connection details withheld.\n")
    print("PASS database migrations")


if __name__ == "__main__":
    main()
