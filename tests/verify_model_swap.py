"""Model-swap verification (DoD #4).

Config-only model change -> different Ollama model serves the answer.
Verifies via /api/ps which model is resident after generation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from config import load_config  # noqa: E402
from rag import ask  # noqa: E402

cfg = load_config()
print(f"Config model: {cfg['model']}")

answer, sources = ask("Can I substitute Cascade hops?")
print(f"Answer: {answer[:200]}")

loaded = [m["name"] for m in requests.get("http://localhost:11434/api/ps", timeout=5).json().get("models", [])]
print(f"Models loaded in Ollama: {loaded}")
assert any(cfg["model"] in m for m in loaded), f"Config model {cfg['model']} not served!"
print("MODEL SWAP OK: config-only change served the answer by a different model")
