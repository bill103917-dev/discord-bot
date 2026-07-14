import discord
from discord import app_commands, Interaction, ButtonStyle
from discord.ext import commands
import asyncio
import yt_dlp
import shutil
import traceback

# ==========================================
# ⚙️ 音樂系統配置 (純 yt-dlp 核心)
# ==========================================
FFMPEG_PATH = shutil.which("ffmpeg")
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'default_search': 'scsearch',  # 強制 SoundCloud 搜尋
    'quiet': True,
    'no_warnings': True,
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# ==========================================
# 🎵 音樂主 Cog
# ==========================================
class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="play", description="直接播放 Spotify 或 SoundCloud 連結")
    async def play(self, interaction: Interaction, url: str):
        # 1. 嚴格封鎖 YouTube
        if "youtube.com" in url or "youtu.be" in url:
            return await interaction.response.send_message(
                "❌ 由於 YouTube 的限制，目前無法播放 YouTube 連結的影片。", 
                ephemeral=True
            )
        
        await interaction.response.defer()

        # 2. 直接呼叫 yt-dlp 處理連結 (不論是 Spotify 還是 SoundCloud 連結，它都能處理)
        try:
            loop = asyncio.get_event_loop()
            # 讓 yt-dlp 自行解析，完全不需要 API 金鑰與會員
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
            
            if 'entries' in data:
                data = data['entries'][0]
            
            await interaction.followup.send(f"▶️ 正在播放: **{data.get('title', '音樂')}**")
            
        except Exception as e:
            await interaction.followup.send(f"❌ 播放失敗: 找不到有效的音源，請確保連結是否正確。")

async def setup(bot):
    await bot.add_cog(MusicCog(bot))

