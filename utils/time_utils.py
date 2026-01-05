# 檔案名稱：utils/time_utils.py
from datetime import datetime, timezone, timedelta

def safe_now():
    """獲取本地時區的格式化時間字串 (亞洲/台北 UTC+8)"""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
