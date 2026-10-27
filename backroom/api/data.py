"""Read adapters expose stored facts without depending on oracle data or model calls."""

import json
from datetime import date, timedelta

from flask import current_app, g, request
from werkzeug.exceptions import BadRequest, NotFound

from backroom.db import connect


def db(*, write=False):
    key = "write_db" if write else "read_db"
    if key not in g:
        repo = connect(
            current_app.config["DATABASE"],
            database_url=current_app.config["DATABASE_URL"],
            readonly=not write,
        )
        setattr(g, key, repo)
    return getattr(g, key)


def close_db(_error=None):
    for key in ("read_db", "write_db"):
        repo = g.pop(key, None)
        if repo:
            repo.close()


def artifact(key):
    return json.loads(current_app.config[key].read_text(encoding="utf-8"))


def unpack(row):
    result = {**json.loads(row.get("payload", "{}")), **row}
    result.pop("payload", None)
    for field in ("refs", "exceptions_by_kind"):
        if isinstance(result.get(field), str):
            result[field] = json.loads(result[field])
    return result


def collection(sql, params=()):
    return [unpack(row) for row in db().rows(sql, params)]


def one(sql, params=()):
    rows = collection(sql, params)
    if not rows:
        raise NotFound("No such record in this snapshot")
    return rows[0]


def integer(name, default, maximum=1000000):
    value = request.args.get(name, str(default))
    if not re_integer(value) or not 1 <= int(value) <= maximum:
        raise BadRequest(f"{name} must be an integer from 1 to {maximum}")
    return int(value)


def re_integer(value):
    return value.isascii() and value.isdigit() and len(value) <= 9


def selection(name, values):
    value = request.args.get(name) or None
    if value is not None and value not in values:
        raise BadRequest(f"Unknown {name}")
    return value


def recon_filters():
    outlet = selection("outlet", {r["id"] for r in db().rows("SELECT id FROM outlets")})
    week = integer("week", 1, 53) if request.args.get("week") else None
    start = date.fromisocalendar(2026, week, 1) if week else None
    return (
        outlet,
        start.isoformat() if start else None,
        ((start + timedelta(days=6)).isoformat() if start else None),
    )


def exceptions(outlet=None, start=None, end=None, kind=None):
    return collection(
        "SELECT * FROM exceptions WHERE (CAST(? AS TEXT) IS NULL OR outlet = ?) "
        "AND (CAST(? AS TEXT) IS NULL OR date_end >= ?) "
        "AND (CAST(? AS TEXT) IS NULL OR date_start <= ?) "
        "AND (CAST(? AS TEXT) IS NULL OR kind = ?) ORDER BY kind, date_start, id",
        (outlet, outlet, start, start, end, end, kind, kind),
    )
