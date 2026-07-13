import discord
from discord import app_commands, Interaction
from discord.ext import commands
import os
import random
import re
from typing import Optional

# 💡 匯入時間工具，若無則使用標準時間備用
try:
    from utils.time_utils import safe_now
except ImportError:
    from datetime import datetime
    def safe_now():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ==========================================
# ⚙️ 預設環境設定（若環境變數中無設定則使用此預設值）
# ==========================================
# 🚨 你的正確私人頻道 ID
DEFAULT_TARGET_CHANNEL_ID = 1446781237422198855  

class ImageDrawCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 統一變數名稱：從環境變數讀取，若無則採用預設值
        self.target_channel_id = int(os.getenv("TARGET_CHANNEL_ID", DEFAULT_TARGET_CHANNEL_ID))
        print("✅ 隨機抽圖系統（純雲端偵測版）已成功啟動。")

    # ==========================================
    # 🖼️ 核心隨機抽圖指令 (已移除本地偵測與排程)
    # ==========================================
    @app_commands.command(name="隨機抽圖", description="從雲端圖庫中隨機抽取一張圖片發送。")
    async def draw_image(self, interaction: discord.Interaction):
        # 0. 先告訴 Discord 我們收到指令了 (避免超時)
        await interaction.response.defer()
        
        # 1. 檢查並取得頻道設定（防快取丟失）
        channel = self.bot.get_channel(self.target_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(self.target_channel_id)
            except Exception as e:
                return await interaction.followup.send(f"❌ 錯誤：找不到指定的圖庫頻道。原因: {e}", ephemeral=True)

        # 2. 收集所有圖片來源
        image_sources = []
        discord_attachments_found = 0
        discord_urls_found = 0
        
        try:
            # 讀取最近 200 條訊息
            async_history = channel.history(limit=200)
            async for msg in async_history:
                # 檢查 1: 訊息自帶的實體附件檔案
                if msg.attachments:
                    for att in msg.attachments:
                        if att.content_type and att.content_type.startswith('image/'):
                            discord_attachments_found += 1
                            image_sources.append({
                                'url': att.url,
                                'name': att.filename or "unknown.png"
                            })
                
                # 檢查 2: 防呆網址貼圖
                elif "http" in msg.content:
                    urls = re.findall(r'(https?://[^\s]+(?:\.png|\.jpg|\.jpeg|\.gif|\.webp))', msg.content, re.IGNORECASE)
                    for url in urls:
                        discord_urls_found += 1
                        image_sources.append({
                            'url': url,
                            'name': url.split('/')[-1] or "web_image.png"
                        })
                        
        except Exception as e:
            print(f"⚠️ 讀取歷史錯誤 (非致命): {e}")

        # 3. 如果完全沒圖，回報偵測清單
        if not image_sources:
            debug_info = (
                "❌ 圖庫目前空空如也！"
            )
            return await interaction.followup.send(debug_info, ephemeral=True)

        # 4. 隨機抽一張
        pick = random.choice(image_sources)

        # 5. 構建 Embed 與發送 (純雲端發送，不需處理本地 File)
        try:
            image_url = pick['url']
            image_name = pick['name']
            
            embed = discord.Embed(
                title="🖼️ 隨機圖庫圖片",
                description="這是從雲端圖庫中隨機挑選的精彩照片！",
                color=discord.Color.blue()
            )
            embed.set_image(url=image_url)
            embed.set_footer(text=f"來源: 頻道歷史 | 檔名: {image_name}")
            
            await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"❌ 發送圖片時發生嚴重錯誤: {e}")
            await interaction.followup.send(f"❌ 發生未預期的錯誤: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ImageDrawCog(bot))