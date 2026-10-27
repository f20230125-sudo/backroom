"""Small demo controls; rate budgets are per instance, not shared across serverless workers."""

import ipaddress
import os
import re
import time
from threading import Lock
from urllib.parse import urlsplit

from flask import request
from werkzeug.exceptions import Forbidden, TooManyRequests

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
READ_ONLY = "Approvals happen in the operator's local copy. This hosted demo is read-only."


def client_address():
    """Vercel overwrites its header; arbitrary proxies and clients cannot supply an identity."""
    if os.environ.get("VERCEL"):
        value = request.headers.get("X-Vercel-Forwarded-For", "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
    return request.remote_addr or "unknown"


def configure_security(app):
    production = app.config["APP_ENV"] == "production"
    if production:
        hosts = app.config.get("TRUSTED_HOSTS") or [
            h.strip() for h in os.environ.get("BACKROOM_TRUSTED_HOSTS", "").split(",") if h.strip()
        ]
        if not hosts or any(
            not re.fullmatch(r"\.?[A-Za-z0-9][A-Za-z0-9.-]*|\[::1\]", h) for h in hosts
        ):
            raise RuntimeError("Set BACKROOM_TRUSTED_HOSTS to explicit production hostnames")
        app.config["TRUSTED_HOSTS"] = hosts
    buckets, lock = {}, Lock()

    @app.before_request
    def controls():
        request.max_content_length = (
            5 * 1024 * 1024 if request.path == "/api/hooks/inbox" else 1024 * 1024
        )
        if request.method != "POST":
            return
        now = time.monotonic()
        window = app.config["RATE_WINDOW"]
        with lock:
            for key in list(buckets):
                if now - buckets[key][0] >= window:
                    del buckets[key]
            key = client_address()
            start, hits = buckets.get(key, (now, 0))
            if hits >= app.config["RATE_LIMIT"]:
                raise TooManyRequests("Request limit reached. Wait before trying again.")
            buckets[key] = (start, hits + 1)
        # Local mutations must also resist cross-origin browser submissions.
        origin = request.headers.get("Origin")
        if request.path != "/api/hooks/inbox" and (
            request.headers.get("Sec-Fetch-Site") == "cross-site"
            or (origin and urlsplit(origin).netloc != request.host)
        ):
            raise Forbidden("Cross-origin submissions are not accepted")

    @app.after_request
    def headers(response):
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response
