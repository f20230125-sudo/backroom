"""Portable, forward-only database migrations."""

from pathlib import Path

from backroom.db.repo import Repo, connect

MIGRATIONS = Path(__file__).parent / "migrations"


def migrate(repo: Repo) -> None:
    if not repo.postgres and not repo.connection.in_transaction:
        repo.execute("BEGIN")
    repo.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
    applied = {row["version"] for row in repo.rows("SELECT version FROM schema_migrations")}
    try:
        for path in sorted(MIGRATIONS.glob("[0-9]*.sql")):
            if path.name in applied:
                continue
            # Migration files contain plain portable DDL, never procedural SQL.
            for statement in path.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    repo.execute(statement)
            repo.execute("INSERT INTO schema_migrations (version) VALUES (?)", (path.name,))
        repo.commit()
    except Exception:
        repo.rollback()
        raise


__all__ = ["Repo", "connect", "migrate"]
