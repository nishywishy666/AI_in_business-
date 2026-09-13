"""Static configuration for the voice receptionist. Every tunable number lives in thresholds.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_yaml(name: str) -> Any:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def thresholds() -> dict[str, Any]:
    return load_yaml("thresholds.yaml")


def threshold(name: str) -> Any:
    values = thresholds()
    if name not in values:
        raise KeyError(f"{name} is not defined in config/thresholds.yaml")
    return values[name]
