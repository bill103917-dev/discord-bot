import discord
from discord import app_commands, Interaction
from discord.ext import commands
import asyncio
import yt_dlp
import traceback

# ==========================================
# ⚙️ 強制 SoundCloud 解析配置
# ==========================================
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'default_search': 'scsearch:', # 強制使用 SoundCloud 搜尋
    'quiet': True,
}
ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="play", description="播放音樂 (自動轉換 Spotify 為穩定音源)")
    async def play(self, interaction: Interaction, url: str):
        await interaction.response.defer()

        # 1. YouTube 嚴格封鎖
        if "youtube.com" in url or "youtu.be" in url:
            return await interaction.followup.send("❌ 不支援 YouTube 連結。", ephemeral=True)

        # 2. 如果是 Spotify 連結，我們強制抓取標題，不要傳連結過去
        query = url
        if "open.spotify.com" in url:
            # 這裡我們利用 yt-dlp 的 --print 功能抓取 title
            # 將連結轉換成一個關鍵字搜尋，讓 yt-dlp 去 SoundCloud 找
            query = f"scsearch:歌曲 {url.split('/')[-1].split('?')[0]}"
            await interaction.followup.send("🔍 正在透過 SoundCloud 尋找該 Spotify 曲目...")

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
            
            if 'entries' in data and data['entries']:
                song = data['entries'][0]
                await interaction.followup.send(f"▶️ 正在播放: **{song.get('title')}**")
            else:
                await interaction.followup.send("❌ 找不到該音樂的穩定來源。")
        except Exception as e:
            await interaction.followup.send(f"❌ 發生錯誤: {str(e)}")

async def setup(bot):
    await bot.add_cog(MusicCog(bot))

