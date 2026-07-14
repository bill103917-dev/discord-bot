import discord
from discord import app_commands, Interaction, ButtonStyle
from discord.ext import commands
import asyncio
import yt_dlp
import os
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import traceback

# ==========================================
# ⚙️ Spotify API 初始化
# ==========================================
SPOTIFY_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

sp = None
if SPOTIFY_ID and SPOTIFY_SECRET:
    try:
        auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_ID, client_secret=SPOTIFY_SECRET)
        sp = spotipy.Spotify(auth_manager=auth_manager)
        print("✅ [音樂系統] Spotify API 成功掛載。")
    except Exception as e:
        print(f"❌ [音樂系統] Spotify API 認證失敗: {e}")

# ==========================================
# ⚙️ 音樂系統配置
# ==========================================
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

    def get_spotify_track_info(self, url: str) -> str:
        """解析 Spotify 連結為 [歌名 歌手] 字串"""
        if not sp: return None
        try:
            track_id = url.split('/')[-1].split('?')[0]
            track = sp.track(track_id)
            return f"{track['name']} {track['artists'][0]['name']}"
        except Exception:
            return None

    @app_commands.command(name="play", description="播放 Spotify 連結 (由 SoundCloud 穩定串流)")
    async def play(self, interaction: Interaction, url: str):
        # 1. YouTube 嚴格封鎖
        if "youtube.com" in url or "youtu.be" in url:
            return await interaction.response.send_message(
                "❌ 由於 YouTube 的限制，目前無法播放 YouTube 連結的影片。請使用 Spotify 連結或歌名搜尋。", 
                ephemeral=True
            )
        
        await interaction.response.defer()

        # 2. Spotify 連結解析
        search_query = url
        if "open.spotify.com" in url:
            meta = self.get_spotify_track_info(url)
            if meta:
                search_query = f"scsearch:{meta}"
            else:
                return await interaction.followup.send("❌ 無法解析 Spotify 資訊，請確認環境變數金鑰是否正確設定。")

        # 3. 搜尋與回饋
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))
            
            if 'entries' in data:
                data = data['entries'][0]
            
            await interaction.followup.send(f"▶️ 正在透過 SoundCloud 播放: **{data.get('title', '音樂')}**")
            
        except Exception as e:
            await interaction.followup.send(f"❌ 播放發生錯誤: {str(e)}")

async def setup(bot):
    await bot.add_cog(MusicCog(bot))