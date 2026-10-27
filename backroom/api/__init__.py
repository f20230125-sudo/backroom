"""HTTP access to pipeline snapshots; app startup never migrates or runs the pipeline."""

import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.utils import safe_join

from backroom import ROOT
from backroom.api.security import configure_security


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_mapping(
        APP_ENV=os.environ.get("APP_ENV", "local"),
        DATABASE=os.environ.get("BACKROOM_DATABASE", str(ROOT / ".local/backroom.sqlite")),
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        RESULTS_PATH=ROOT / "eval/results.json",
        ASK_GOLD_PATH=ROOT / "eval/ask_gold.json",
        PUBLIC_DIR=ROOT / "public",
        INBOX_WEBHOOK_SECRET=os.environ.get("INBOX_WEBHOOK_SECRET", ""),
        INBOX_DATABASE_URL=os.environ.get("INBOX_DATABASE_URL", ""),
        RATE_LIMIT=60,
        RATE_WINDOW=60,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if config:
        app.config.update(config)
    configure_security(app)
    from backroom.api.data import close_db
    from backroom.api.hooks import bp as hooks
    from backroom.api.routes import bp

    app.teardown_appcontext(close_db)
    app.register_blueprint(bp)
    app.register_blueprint(hooks)

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = jsonify(error=error.name.lower().replace(" ", "_"), message=error.description)
        response.status_code = error.code or 500
        if error.code == 429:
            response.headers["Retry-After"] = str(app.config["RATE_WINDOW"])
        return response

    @app.errorhandler(Exception)
    def unexpected_error(_error):
        # DB exceptions can embed credentials or source text; never log their strings.
        return jsonify(error="unavailable", message="Snapshot unavailable. Try again later."), 503

    @app.route("/api", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_api(path):
        raise NotFound("No such API endpoint")

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def shell(path):
        public = Path(app.config["PUBLIC_DIR"])
        candidate = safe_join(str(public), path)
        if path and candidate and Path(candidate).is_file():
            return send_from_directory(
                public, path, max_age=31536000 if path.startswith("assets/") else 0
            )
        if not (public / "index.html").is_file():
            return jsonify(error="not_built", message="Run npm --prefix web run build"), 503
        return send_from_directory(public, "index.html", max_age=0)

    return app
