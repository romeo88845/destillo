import json
import os
from typing import Dict, Any

CONFIG_PATH = "/opt/destillo/data/config.json"

DEFAULT_CONFIG = {
    "llm_provider": "local",
    "local_llm_url": "https://opencode.ai/zen",
    "local_llm_model": "deepseek-v4-flash-free",
    "subject_areas": [],
    "default_subject_area": "misc",
    "default_storage_path": "/data/kb/destillo"
}


def load_config() -> Dict[str, Any]:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_PATH) as f:
        stored = json.load(f)
    config = DEFAULT_CONFIG.copy()
    config.update(stored)
    return config


def save_config(config: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get_subject_area_path(config: Dict, subject_area: str) -> str:
    for sa in config.get("subject_areas", []):
        if sa["name"].lower() == subject_area.lower():
            return sa["path"]
    return os.path.join(
        config.get("default_storage_path", "/data/kb/destillo"),
        subject_area
    )
