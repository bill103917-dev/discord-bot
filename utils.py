import os
import json
from datetime import datetime, timezone, timedelta


CONFIG_DIR = "configs" 
SUPPORT_CONFIG_FILE = "support_config.json" # SupportCog 用的配置檔名

# --- 1. 時間工具 ---

def safe_now():
    """獲取本地時區的格式化時間字串 (例如：台北時區)"""
    # 這裡假設您希望使用台灣/亞洲時區 (UTC+8)
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


# --- 2. 通用配置讀寫工具 (用於伺服器個別配置) ---

def get_config_path(guild_id):
    """建構特定伺服器的配置檔案路徑"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    return os.path.join(CONFIG_DIR, f"{guild_id}.json")

def load_config(guild_id: int) -> dict:
    """載入伺服器設定 (用於 Flask Web)"""
    path = get_config_path(guild_id)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        print(f"[ERROR] 伺服器 {guild_id} 的設定檔案損壞。")
        return {}
    
def save_config(guild_id: int, config: dict):
    """儲存伺服器設定 (用於 Flask Web)"""
    path = get_config_path(guild_id)
    with open(path, 'w', encoding='utf-8') as f:
        # ensure_ascii=False 確保中文可以正常儲存
        json.dump(config, f, indent=4, ensure_ascii=False)




def get_support_config_path():
    """建構全域支援配置檔案路徑"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    return os.path.join(CONFIG_DIR, SUPPORT_CONFIG_FILE)

def load_support_config() -> dict:
    """載入全域支援設定 (用於 SupportCog)"""
    path = get_support_config_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # 如果檔案不存在或損壞，返回空字典
        return {}

def save_support_config(config: dict):
    """儲存全域支援設定 (用於 SupportCog)"""
    path = get_support_config_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
