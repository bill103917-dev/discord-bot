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
from typing import Optional, List, Tuple

# =========================
# -- 1. Spotify 工具函數
# =========================
def get_spotify_track_info(url: str):
    """解析 Spotify 連結並回傳 '歌曲名 歌手' 用於搜尋"""
    if "open.spotify.com/track" not in url:
        return None
    
    try:
        cid = os.getenv("SPOTIFY_CLIENT_ID")
        csc = os.getenv("SPOTIFY_CLIENT_SECRET")
        if not cid or not csc:
            return None
            
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=cid, client_secret=csc))
        track = sp.track(url)
        return f"{track['name']} {track['artists'][0]['name']}"
    except Exception as e:
        print(f"❌ Spotify 解析錯誤: {e}")
        return None

# =========================
# -- 2. 介面元件 (Views)
# =========================
class EndOfQueueView(ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id

    @ui.button(label="繼續留在頻道", style=discord.ButtonStyle.primary)
    async def keep_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message("✅ 機器人會保留在語音頻道。", ephemeral=True)
        try: await interaction.message.delete()
        except: pass

    @ui.button(label="離開語音頻道", style=discord.ButtonStyle.danger)
    async def leave_button(self, interaction: Interaction, button: ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc: await vc.disconnect()
        self.cog.clear_guild_data(self.guild_id)
        await interaction.response.send_message("👋 已離開語音頻道。", ephemeral=True)
        try: await interaction.message.delete()
        except: pass

class MusicControlView(ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        vc = self.cog.vc_dict.get(self.guild_id)
        if not vc or not vc.is_connected():
            return interaction.user.guild_permissions.administrator
        if interaction.user.voice and interaction.user.voice.channel == vc.channel:
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("❌ 你必須與機器人在同一個語音頻道！", ephemeral=True)
        return False

    @ui.button(label="⏯️", style=discord.ButtonStyle.primary)
    async def btn_pause_resume(self, interaction: Interaction, button: ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc.is_playing(): vc.pause()
        else: vc.resume()
        await interaction.response.defer()
        await self.cog.update_control_message(self.guild_id)

    @ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def btn_skip(self, interaction: Interaction, button: ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc: vc.stop()
        await interaction.response.send_message("⏩ 已跳過", ephemeral=True)

    @ui.button(label="⏹️", style=discord.ButtonStyle.danger)
    async def btn_stop(self, interaction: Interaction, button: ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc: await vc.disconnect()
        self.cog.clear_guild_data(self.guild_id)
        await interaction.response.send_message("⏹️ 已停止並離開", ephemeral=True)

    @ui.button(label="🔊 +", style=discord.ButtonStyle.success)
    async def btn_vol_up(self, interaction: Interaction, button: ui.Button):
        new = min(1.0, self.cog.current_volume.get(self.guild_id, 0.5) + 0.1)
        self.cog.current_volume[self.guild_id] = new
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = new
        await interaction.response.send_message(f"🔊 音量 {int(new*100)}%", ephemeral=True)
        await self.cog.update_control_message(self.guild_id)

    @ui.button(label="🔇 -", style=discord.ButtonStyle.danger)
    async def btn_vol_down(self, interaction: Interaction, button: ui.Button):
        new = max(0.0, self.cog.current_volume.get(self.guild_id, 0.5) - 0.1)
        self.cog.current_volume[self.guild_id] = new
        if interaction.guild.voice_client and interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = new
        await interaction.response.send_message(f"🔇 音量 {int(new*100)}%", ephemeral=True)
        await self.cog.update_control_message(self.guild_id)

# =========================
# -- 3. Music System Cog
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

    async def extract_audio(self, query: str):
        # Spotify 預處理
        spotify_search = get_spotify_track_info(query)
        search_target = spotify_search if spotify_search else query

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
            
            title = info.get("title", "未知曲目")
            if spotify_search: title = f"🎧 {title} (Spotify)"
            elif "soundcloud.com" in query: title = f"☁️ {title} (SoundCloud)"
            
            return info.get("url"), title, int(info.get("duration", 0)), info.get("thumbnail"), info.get("webpage_url")
        except Exception as e:
            print(f"❌ 提取錯誤: {e}")
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
            fut = asyncio.run_coroutine_threadsafe(self.playback_finished(guild_id, error), self.bot.loop)
            try: fut.result()
            except: pass

        vc.play(source, after=after_callback)
        await self.update_control_message(guild_id)

    async def playback_finished(self, guild_id, error):
        self.now_playing.pop(guild_id, None)
        if self.queue.get(guild_id):
            await self.start_playback(guild_id)
        else:
            # 隊列結束，詢問是否離開
            guild = self.bot.get_guild(guild_id)
            if guild and (chan := self._get_best_channel(guild)):
                embed = discord.Embed(title="🎶 播放完畢", description="清單已空，要讓機器人留下來嗎？", color=0x3498db)
                await chan.send(embed=embed, view=EndOfQueueView(self, guild_id))

    def _get_best_channel(self, guild):
        return guild.text_channels[0] if guild.text_channels else None

    async def update_control_message(self, guild_id: int, channel=None):
        vc = self.vc_dict.get(guild_id)
        now = self.now_playing.get(guild_id)
        q = self.queue.get(guild_id, [])
        target_channel = channel or (vc.channel.guild.text_channels[0] if vc else None)
        if not target_channel: return

        embed = discord.Embed(title="🎶 音樂播放器", color=discord.Color.blue())
        embed.add_field(name="狀態", value="▶️ 播放中" if vc and vc.is_playing() else "⏸️ 已暫停" if vc and vc.is_paused() else "⏹️ 停止", inline=True)
        
        if now:
            title, dur, thumb, url = now
            embed.add_field(name="現在播放", value=f"**[{title}]({url})**\n長度: `{dur}s` | 音量: `{int(self.current_volume.get(guild_id, 0.5)*100)}%`", inline=False)
            if thumb: embed.set_image(url=thumb)
        
        if q:
            embed.add_field(name=f"待播放 ({len(q)})", value="\n".join([f"{i+1}. {x[1]}" for i, x in enumerate(q[:5])]), inline=False)

        view = MusicControlView(self, guild_id)
        msg_id = self.control_messages.get(guild_id)
        try:
            if msg_id:
                msg = await target_channel.fetch_message(msg_id)
                await msg.edit(embed=embed, view=view)
            else:
                msg = await target_channel.send(embed=embed, view=view)
                self.control_messages[guild_id] = msg.id
        except:
            msg = await target_channel.send(embed=embed, view=view)
            self.control_messages[guild_id] = msg.id

    @app_commands.command(name="play", description="播放音樂 (支援 YouTube, Spotify, SoundCloud)")
    async def play(self, interaction: Interaction, query: str):
        await interaction.response.defer()
        if not interaction.user.voice: return await interaction.followup.send("❌ 請先加入語音頻道")
        
        vc = interaction.guild.voice_client
        if not vc: vc = await interaction.user.voice.channel.connect()
        self.vc_dict[interaction.guild.id] = vc
        
        url, title, dur, thumb, web = await self.extract_audio(query)
        if not url: return await interaction.followup.send("❌ 無法讀取該來源")

        self.queue.setdefault(interaction.guild.id, []).append((url, title, dur, thumb, web))
        if not vc.is_playing() and not vc.is_paused():
            await self.start_playback(interaction.guild.id)
        
        await interaction.followup.send(f"✅ 已加入隊列: **{title}**")

async def setup(bot): 
    await bot.add_cog(MusicCog(bot))
