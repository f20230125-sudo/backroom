"""Vercel entrypoint: API plus public/ assets and React deep links."""

from backroom.api import create_app

app = create_app()
