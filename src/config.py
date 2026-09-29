"""Load config.yaml and resolve paths relative to the repo root."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | str = ROOT / "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def repo_path(rel: str) -> Path:
    return ROOT / rel
