"""Shared configuration loader for brew-assistant.

Reads config.yaml once and resolves relative paths against the project root.
The Ollama model name lives only in config.yaml — never hardcode it in code.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    """Load config.yaml and resolve data/chroma paths to absolute paths."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("data_dir", "chroma_dir"):
        path = Path(cfg[key])
        if not path.is_absolute():
            cfg[key] = ROOT / path
    return cfg
