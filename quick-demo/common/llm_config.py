import json
from pathlib import Path
from typing import Dict


LLM_CONFIG_FILE = Path(__file__).resolve().parents[1] / "llm_config.json"


def load_llm_config() -> Dict:
    if not LLM_CONFIG_FILE.exists():
        return {}
    with open(LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def has_llm_config(config: Dict = None) -> bool:
    config = config if config is not None else load_llm_config()
    return all(str(config.get(key, "")).strip() for key in ("API_KEY", "URL", "MODEL"))
