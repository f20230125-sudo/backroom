"""The web gate builds locally and tests the API against isolated offline databases."""

import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from backroom import ROOT
from backroom.api import create_app
from backroom.api.security import CSP
from backroom.db import connect, migrate


def checks():
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise ValueError("Installed npm toolchain is unavailable")
    built = subprocess.run(  # noqa: S603 -- fixed local build, no package installation
        [npm, "--prefix", "web", "run", "build"],
        cwd=ROOT,
        check=False,
    )
    if built.returncode:
        raise ValueError("Web build failed")
    print("PASS m5 web production build", flush=True)
    if not (ROOT / "public/index.html").is_file():
        raise ValueError("public/index.html missing after build")
    print("PASS m5 public/index.html exists", flush=True)
    html = (ROOT / "public/index.html").read_text(encoding="utf-8")
    stylesheets = re.findall(r'href="(/assets/[^"\s]+\.css)"', html)
    if not stylesheets or re.search(r"<style\b|\sstyle=", html, re.IGNORECASE):
        raise ValueError("App shell must use static stylesheets under the self-only CSP")
    if not all((ROOT / "public" / name.lstrip("/")).is_file() for name in stylesheets):
        raise ValueError("Bundled stylesheet missing")
    print("PASS m5 static stylesheet assets under self-only CSP", flush=True)
    tested = subprocess.run(  # noqa: S603 -- fixed contract suite, scratch database paths
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_api.py",
            "-o",
            "addopts=",
            "-q",
            "--basetemp=.local/codex-gate-pytest",
        ],
        cwd=ROOT,
        check=False,
    )
    if tested.returncode:
        raise ValueError("API contract suite failed")
    print("PASS m5 API contract suite", flush=True)
    scratch = ROOT / ".local"
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="codex-m5-gate-", dir=scratch) as directory:
        from pathlib import Path

        path = Path(directory) / "gate.sqlite"
        with connect(path) as repo:
            migrate(repo)
        client = create_app(
            {
                "APP_ENV": "production",
                "DATABASE": path,
                "DATABASE_URL": "",
                "TRUSTED_HOSTS": ["localhost"],
            }
        ).test_client()
        for endpoint in ("/api/health", "/api/missing", "/inbox/example"):
            response = client.get(endpoint)
            if response.headers.get("Content-Security-Policy") != CSP or not all(
                key in response.headers
                for key in (
                    "X-Content-Type-Options",
                    "Referrer-Policy",
                    "Strict-Transport-Security",
                )
            ):
                raise ValueError("Security headers missing")
        print("PASS m5 security headers on API, errors and app shell", flush=True)
        for action in ("/names/supplier/approve", "/names/supplier/reject", "/review/any/resolve"):
            response = client.post("/api" + action, json={})
            if response.status_code != 403 or not response.is_json:
                raise ValueError("Hosted mutation guard failed")
        print("PASS m5 hosted read-only guard", flush=True)
