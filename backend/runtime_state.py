"""Runtime bot state — flags that can be toggled at runtime via the UI
instead of via env vars + container restarts."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_state = {
    "mock_mode": os.environ.get("BOT_MOCK_MODE", "false").lower() == "true",
}


def is_mock_mode() -> bool:
    return bool(_state["mock_mode"])


def set_mock_mode(value: bool) -> None:
    _state["mock_mode"] = bool(value)


def snapshot() -> dict:
    return dict(_state)
