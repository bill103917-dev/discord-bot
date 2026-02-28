import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
from yt_dlp import YoutubeDL
import asyncio
import os
import tempfile
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from typing import Optional, List

# =========================
# -- 1. Spotify 處理工具 --
# =========================
def get_spotify_info(url: str) -> List[str]:
    """解析 Spotify 連結並回傳搜尋關鍵字清單"""
    if "open.spotify.com" not in url:
        return []
    
    try:
        cid = os.getenv("SPOTIFY_CLIENT_ID")
        csc = os.getenv("SPOTIFY_CLIENT_SECRET")
        if not cid or not csc:
            print("❌ 系統提示: 尚未設定 Spotify API 憑證，無法解析連結。")
            return []
            
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=cid, client_secret=csc))
        
        # 辨識類型與 ID (track/album/playlist)
        match = re.search(r"(track|album|playlist)/([a-zA-Z0-9]+)", url)
        if not match: return []
        
        link_type, spotify_id = match.groups()
        keywords = []

        if link_type == "track":
            t = sp.track(spotify_id)
            keywords.append(f"{t['name']} {t['artists'][0]['name']}")
        
        elif link_type == "album":
            album = sp.album(spotify_id)
            for item in album['tracks']['items']:
                keywords.append(f"{item['name']} {album['artists'][0]['name']}")
        
        elif link_type == "playlist":
            results = sp.playlist_items(spotify_id)
            items = results['items']
            # 處理分頁以防清單太長
            while results['next']:
                results = sp.next(results)
                items.extend(results['items'])
            
            for item in items:
                if item.get('track'):
                    t = item['track']
                    keywords.append(f"{t['name']} {t['artists'][0]['name']}")
                    
        return keywords
    except Exception as e:
        print(f"❌ Spotify 解析錯誤: {e}")
        return []

# =========================
# -- 2. UI 介面元件 --
# =========================
class MusicControlView(ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @ui.button(label="⏯️ 暫停/繼續", style=discord.ButtonStyle.primary)
    async def btn_pause_resume(self, interaction: Interaction, button: ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if not vc: return await interaction.response.send_message("❌ 機器人不在頻道中", ephemeral=True)
        
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ 已暫停播放", ephemeral=True)
        else:
            vc.resume()
            await interaction.response.send_message("▶️ 繼續播放", ephemeral=True)
        await self.cog.update_control_message(self.guild_id)

    @ui.button(label="⏭️ 跳過", style=discord.ButtonStyle.secondary)
    async def btn_skip(self, interaction: Interaction, button: ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc: vc.stop()
        await interaction.response.send_message("⏩ 已跳過當前歌曲", ephemeral=True)

    @ui.button(label="⏹️ 停止", style=discord.ButtonStyle.danger)
    async def btn_stop(self, interaction: Interaction, button: ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc: await vc.disconnect()
        self.cog.clear_guild_data(self.guild_id)
        await interaction.response.send_message("⏹️ 已停止播放並清空清單", ephemeral=True)

# =========================
# -- 3. 音樂系統主 Cog --
# =========================
class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue = {}             # {gid: [data]}
        self.now_playing = {}       # {gid: data}
        self.vc_dict = {}           # {gid: vc}
        self.current_volume = {}    # {gid: float}
        self.control_messages = {}  # {gid: msg_id}
        self.cookies_file = self._load_cookies()

    def _load_cookies(self):
        cookie_data = os.getenv("YOUTUBE_COOKIES")
        if cookie_data:
            tf = tempfile.NamedTemporaryFile(delete=False, prefix="yt_cookies_", suffix=".txt")
            tf.write(cookie_data.encode("utf-8"))
            tf.close()
            return tf.name
        return None

    def clear_guild_data(self, guild_id):
        self.vc_dict.pop(guild_id, None)
        self.queue.pop(guild_id, None)
        self.now_playing.pop(guild_id, None)
        self.current_volume.pop(guild_id, None)
        self.control_messages.pop(guild_id, None)

    async def extract_audio(self, search_target: str):
        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "default_search": "ytsearch1" if "http" not in search_target else None,
            "nocheckcertificate": True,
            "cookiefile": self.cookies_file if self.cookies_file else None
        }
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: YoutubeDL(ydl_opts).extract_info(search_target, download=False))
            if "entries" in info: info = info["entries"][0]
            return info.get("url"), info.get("title"), int(info.get("duration", 0)), info.get("thumbnail"), info.get("webpage_url")
        except Exception as e:
            print(f"❌ YouTube 提取錯誤: {e}")
            return None, None, 0, None, None

    async def start_playback(self, guild_id: int):
        vc = self.vc_dict.get(guild_id)
        if not vc or not self.queue.get(guild_id): return

        audio_url, title, duration, thumb, webpage = self.queue[guild_id].pop(0)
        self.now_playing[guild_id] = (title, duration, thumb, webpage)
        vol = self.current_volume.get(guild_id, 0.5)

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(audio_url, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", options="-vn"),
            vol
        )

        def after_callback(error):
            asyncio.run_coroutine_threadsafe(self.playback_finished(guild_id, error), self.bot.loop)

        vc.play(source, after=after_callback)
        await self.update_control_message(guild_id)

    async def playback_finished(self, guild_id, error):
        if self.queue.get(guild_id):
            await self.start_playback(guild_id)
        else:
            self.now_playing.pop(guild_id, None)
            await self.update_control_message(guild_id)

    async def update_control_message(self, guild_id: int):
        vc = self.vc_dict.get(guild_id)
        now = self.now_playing.get(guild_id)
        q = self.queue.get(guild_id, [])
        
        # 取得頻道來發送訊息
        msg_id = self.control_messages.get(guild_id)
        # 簡單起見，我們找第一個可用的文字頻道或原本發指令的地方
        # 實務上建議記錄下 play 指令發送時的 channel_id

        embed = discord.Embed(title="🎶 音樂控制面板", color=discord.Color.green())
        if now:
            title, dur, thumb, url = now
            embed.description = f"**正在播放:** [{title}]({url})\n**時長:** `{dur}s` | **音量:** `{int(self.current_volume.get(guild_id, 0.5)*100)}%`"
            if thumb: embed.set_image(url=thumb)
        else:
            embed.description = "目前沒有播放中的歌曲"

        if q:
            list_str = "\n".join([f"{i+1}. {x[1]}" for i, x in enumerate(q[:5])])
            embed.add_field(name=f"待播放清單 (餘 {len(q)} 首)", value=list_str, inline=False)

        # 這裡需要一個 channel 來發送/編輯。簡易處理：
        # 如果需要讓面板固定更新，建議在 play 指令傳入 interaction.channel
        pass

    @app_commands.command(name="play", description="播放音樂 (支援 YouTube, Spotify)")
    async def play(self, interaction: Interaction, query: str):
        await interaction.response.defer()
        if not interaction.user.voice: 
            return await interaction.followup.send("❌ 你必須先進入語音頻道")
        
        # 檢查 Spotify
        spotify_targets = get_spotify_info(query)
        targets = spotify_targets if spotify_targets else [query]

        vc = interaction.guild.voice_client
        if not vc: 
            vc = await interaction.user.voice.channel.connect()
            self.vc_dict[interaction.guild.id] = vc
            self.current_volume[interaction.guild.id] = 0.5

        added_count = 0
        for t in targets:
            url, title, dur, thumb, web = await self.extract_audio(t)
            if url:
                self.queue.setdefault(interaction.guild.id, []).append((url, title, dur, thumb, web))
                added_count += 1
                if not vc.is_playing() and not vc.is_paused():
                    await self.start_playback(interaction.guild.id)

        if added_count > 0:
            msg = f"✅ 已成功加入 **{added_count}** 首歌曲！" if added_count > 1 else f"✅ 已加入隊列: **{title}**"
            await interaction.followup.send(msg)
        else:
            await interaction.followup.send("❌ 無法抓取音訊來源")

async def setup(bot): 
    await bot.add_cog(MusicCog(bot))
