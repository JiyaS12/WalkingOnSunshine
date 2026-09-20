"""Real main app with an isolated synthetic store; subprocess entrypoint only."""

import os
from pathlib import Path

import agent
import store
from main import app

store.reset_for_tests(Path(os.environ["FIXTURE_STORE"]))
agent.CACHE_PATH = Path(os.environ["FIXTURE_CACHE"])

__all__ = ["app"]
