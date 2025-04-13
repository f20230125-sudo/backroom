"""Authenticate raw email bytes before durable ingestion; replay insertion is atomic."""

import base64
import hashlib
import hmac
import re
import time
from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import (
    BadRequest,
    Conflict,
    Forbidden,
    ServiceUnavailable,
    UnsupportedMediaType,
)

from backroom.api.data import db
from backroom.db import connect
from backroom.db.repo import dumps

bp = Blueprint("hooks", __name__, url_prefix="/api/hooks")


@bp.post("/inbox")
def inbox():
    if request.mimetype != "message/rfc822":
        raise UnsupportedMediaType("Send message/rfc822, up to 5 MB")
    raw = request.get_data()
    secret = current_app.config["INBOX_WEBHOOK_SECRET"]
    if not secret:
        raise ServiceUnavailable("Inbox webhook is not configured")
    timestamp = request.headers.get("X-Backroom-Timestamp", "")
    signature = request.headers.get("X-Backroom-Signature", "")
    if not re.fullmatch(r"[0-9]{1,12}", timestamp):
        raise BadRequest("Invalid webhook timestamp")
    if abs(time.time() - int(timestamp)) > 300:
        raise Forbidden("Webhook timestamp is outside the five-minute window")
    expected = (
        "sha256="
        + hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    )
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature) or not hmac.compare_digest(
        expected, signature
    ):
        raise Forbidden("Invalid webhook signature")
    digest = hashlib.sha256(signature.encode()).hexdigest()
    now = datetime.now(UTC).isoformat()
    # Hosted business connections stay read-only. Ingress may use a separately granted
    # role with INSERT on inbox/webhook_replays only; it must target the pipeline DB.
    production = current_app.config["APP_ENV"] == "production"
    if production and not current_app.config["INBOX_DATABASE_URL"]:
        raise ServiceUnavailable("Hosted inbox ingestion requires INBOX_DATABASE_URL")
    repo = (
        connect(database_url=current_app.config["INBOX_DATABASE_URL"])
        if production
        else db(write=True)
    )
    try:
        changed = repo.execute(
            "INSERT INTO webhook_replays (signature_hash, received_at) VALUES (?, ?) "
            "ON CONFLICT (signature_hash) DO NOTHING",
            (digest, now),
        ).rowcount
        if not changed:
            raise Conflict("This webhook has already been received")
        repo.execute(
            "INSERT INTO inbox (id, signature_hash, payload) VALUES (?, ?, ?)",
            (
                digest,
                digest,
                dumps(
                    {
                        "raw_base64": base64.b64encode(raw).decode("ascii"),
                        "received_at": now,
                        "status": "pending",
                        "media_type": "message/rfc822",
                    }
                ),
            ),
        )
        repo.commit()
    finally:
        if production:
            repo.close()
    return jsonify(id=digest, status="pending"), 202
