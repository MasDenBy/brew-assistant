"""Offline checks for model selection (config `models:` map + UI helpers).

Covers:
- config.model_settings: per-model overrides win, globals fall through,
  unknown/absent models get the global settings
- app.model_choices: dropdown order (config shortlist in config order, pulled ->
  unpulled -> other installed), config default always selectable, legacy fallback
- app.last_model_of: mid-chat model-switch detection
- rag.get_llm: precedence UI-arg > per-model override > global; num_ctx is
  config-only (no network calls)

Run: .venv\\Scripts\\python.exe tests\\test_model_selection.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import last_model_of, model_choices  # noqa: E402
from config import load_config, model_settings  # noqa: E402
from rag import get_llm  # noqa: E402

GLOBAL = {"model": "m0", "temperature": 0.3, "num_ctx": 8192}


def test_model_settings() -> None:
    # per-model overrides win over globals
    cfg = GLOBAL | {"models": {"m1": {"num_ctx": 16384, "temperature": 0.2}}}
    assert model_settings(cfg, "m1") == {"temperature": 0.2, "num_ctx": 16384}
    globals_ok = {"temperature": 0.3, "num_ctx": 8192}

    # partial override: only the overridden key changes
    cfg = GLOBAL | {"models": {"m1": {"num_ctx": 4096}}}
    assert model_settings(cfg, "m1") == {"temperature": 0.3, "num_ctx": 4096}

    # unknown model, empty and absent `models:` -> globals unchanged
    cfg = GLOBAL | {"models": {"m1": {"num_ctx": 4096}}}
    assert model_settings(cfg, "unknown") == globals_ok
    cfg = GLOBAL | {"models": {}}
    assert model_settings(cfg, "m0") == globals_ok
    assert model_settings(GLOBAL, "m0") == globals_ok


def test_model_choices() -> None:
    cfg_default = "qwen2.5:7b"

    # legacy fallback (no `models:`): every installed model, default first
    cfg = {"model": cfg_default}
    assert model_choices(["m:1", "m:2"], cfg) == [cfg_default, "m:1", "m:2"]
    assert model_choices([cfg_default], cfg) == [cfg_default]
    assert model_choices([], cfg) == [cfg_default]

    # with `models:`: shortlist in config order (user's curated order),
    # pulled before unpulled, then remaining installed models (alphabetical)
    cfg = {
        "model": "zz:7b",
        "models": {"zz:7b": {}, "alpha:7b": {}, "ghost:7b": {}},  # ghost unpulled
    }
    installed = ["alpha:7b", "other:8b", "zz:7b"]
    assert model_choices(installed, cfg) == ["zz:7b", "alpha:7b", "ghost:7b", "other:8b"]

    # default not in the shortlist and not installed is prepended
    cfg = {"model": "spare:1b", "models": {"alpha:7b": {}}}
    assert model_choices(["alpha:7b"], cfg) == ["spare:1b", "alpha:7b"]

    # default model absent from the shortlist is still selectable
    cfg = {"model": cfg_default, "models": {"alpha:7b": {}}}
    names = model_choices([cfg_default], cfg)
    assert cfg_default in names and "alpha:7b" in names, names

    # default absent from Ollama entirely still appears (as unpulled)
    assert model_choices([], cfg) == [cfg_default, "alpha:7b"]


def test_last_model_of() -> None:
    assert last_model_of([]) is None
    assert last_model_of([{"role": "user", "content": "q"}]) is None
    assert last_model_of([{"role": "assistant", "content": "a"}]) is None
    history = [
        {"role": "assistant", "content": "a1", "model": "m1"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a2", "model": "m2"},
    ]
    assert last_model_of(history) == "m2"


def test_get_llm_precedence() -> None:
    real_cfg = load_config()

    # per-model num_ctx override resolves through the real config.yaml
    qwen_ctx = (real_cfg["models"] or {}).get(real_cfg["model"], {}).get("num_ctx")
    if qwen_ctx is None:
        qwen_ctx = real_cfg["num_ctx"]
    llm = get_llm()
    assert llm.num_ctx == qwen_ctx, (llm.num_ctx, qwen_ctx)
    assert llm.model == real_cfg["model"]
    assert abs(llm.temperature - real_cfg["temperature"]) < 1e-9

    # an explicit temperature (UI slider) always wins over per-model overrides
    model = next(iter(real_cfg["models"] or {"off-model:1": {}}))
    llm = get_llm(model=model, temperature=0.7)
    assert abs(llm.temperature - 0.7) < 1e-9, llm.temperature
    assert llm.model == model


def main() -> None:
    test_model_settings()
    print("PASS: model_settings merge order (override > global; unknown -> global)")
    test_model_choices()
    print("PASS: model_choices ordering, shortlist, legacy fallback, default present")
    test_last_model_of()
    print("PASS: last_model_of switch detection")
    test_get_llm_precedence()
    print("PASS: get_llm precedence (UI arg > per-model > global; num_ctx config-only)")


if __name__ == "__main__":
    main()
