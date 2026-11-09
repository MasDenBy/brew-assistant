"""Shared configuration loader for brew-assistant.

Reads config.yaml once and resolves relative paths against the project root.
The Ollama model name lives only in config.yaml — never hardcode it in code.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    """Load config.yaml and resolve path values to absolute paths.

    Resolves single paths (`data_dir`, `chroma_dir`, `collect_dest`) and the
    `data_dirs` list (relative entries resolve against the project root;
    absolute entries — e.g. a UNC share — are kept as-is).
    """
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("data_dir", "chroma_dir", "collect_dest"):
        if key in cfg:
            path = Path(cfg[key])
            if not path.is_absolute():
                cfg[key] = ROOT / path
    if "data_dirs" in cfg:
        cfg["data_dirs"] = [
            Path(entry) if Path(entry).is_absolute() else ROOT / Path(entry)
            for entry in cfg["data_dirs"]
        ]
    return cfg
