# 檔案名稱：utils/config_manager.py
import os
import json

CONFIG_DIR = "configs" 
SUPPORT_CONFIG_FILE = "support_config.json"

def get_config_path(guild_id: int):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    return os.path.join(CONFIG_DIR, f"{guild_id}.json")

def load_config(guild_id: int) -> dict:
    path = get_config_path(guild_id)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(guild_id: int, config: dict):
    path = get_config_path(guild_id)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def load_support_config() -> dict:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    path = os.path.join(CONFIG_DIR, SUPPORT_CONFIG_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_support_config(config: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    path = os.path.join(CONFIG_DIR, SUPPORT_CONFIG_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
