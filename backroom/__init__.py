"""Backroom: a back-office agent for a restaurant group (synthetic data)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
# The model response cache is committed (it holds synthetic documents only), so the pipeline and the
# evaluation replay with no key and no calls.
CACHE_DB = ROOT / "cache" / "llm.sqlite"
