import discord
from discord import app_commands, Interaction, ButtonStyle
from discord.ext import commands
import asyncio
import yt_dlp
import os
import random

# ==========================================
# ⚙️ YouTube Cookie 安全載入機制
# ==========================================
COOKIE_FILE_PATH = None
YT_COOKIES_CONTENT = os.getenv("YT_COOKIES")

if YT_COOKIES_CONTENT:
    COOKIE_FILE_PATH = "temp_youtube_cookies.txt"
    try:
        with open(COOKIE_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(YT_COOKIES_CONTENT)
        print("✅ [音樂系統] 已成功從環境變數載入 YouTube Cookie！")
    except Exception as e:
        print(f"❌ [音樂系統] 寫入 Cookie 失敗: {e}")
        COOKIE_FILE_PATH = None

# ==========================================
# ⚙️ yt-dlp 與 FFmpeg 終極安全配置
# ==========================================
YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    # 🚀 【核心修正】模擬 iOS 與 Android 用戶端，繞過網頁版 PoToken 與 IP 限制！
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'android'],
        }
    },
}

# 備用降級配置
FALLBACK_FORMAT_OPTIONS = YTDL_FORMAT_OPTIONS.copy()
FALLBACK_FORMAT_OPTIONS['format'] = 'best'

if COOKIE_FILE_PATH:
    YTDL_FORMAT_OPTIONS['cookiefile'] = COOKIE_FILE_PATH
    FALLBACK_FORMAT_OPTIONS['cookiefile'] = COOKIE_FILE_PATH

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)
ytdl_fallback = yt_dlp.YoutubeDL(FALLBACK_FORMAT_OPTIONS)


# ==========================================
# 🛡️ 雙重安全解析輔助函式
# ==========================================
async def safe_extract_info(loop, url, download=False, process=False):
    """
    安全解析 YouTube 影片資訊。
    採用 iOS/Android 用戶端模擬，並在極端情況下自動進行格式降級。
    """
    def _extract():
        try:
            return ytdl.extract_info(url, download=download, process=process)
        except yt_dlp.utils.DownloadError as e:
            if "Requested format is not available" in str(e):
                print(f"⚠️ [音樂系統] 偵測到特殊限制，啟動安全備用降級解析...")
                return ytdl_fallback.extract_info(url, download=download, process=process)
            raise e

    return await loop.run_in_executor(None, _extract)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail')
        self.webpage_url = data.get('webpage_url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await safe_extract_info(loop, url, download=not stream, process=True)
        
        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)


# ==========================================
# 🎛️ 音樂控制台按鈕介面 (Discord UI View)
# ==========================================
class MusicControlView(discord.ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        if not interaction.user.voice or not interaction.guild.voice_client:
            await interaction.response.send_message("❌ 您必須與機器人在同一個語音頻道才能操作！", ephemeral=True)
            return False
        if interaction.user.voice.channel != interaction.guild.voice_client.channel:
            await interaction.response.send_message("❌ 您必須與機器人在同一個語音頻道才能操作！", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="⏪", style=ButtonStyle.secondary, custom_id="btn_replay", row=0)
    async def replay_button(self, interaction: Interaction, button: discord.ui.Button):
        """重播當前歌曲"""
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and self.cog.current_track.get(self.guild_id):
            current = self.cog.current_track[self.guild_id]
            queue = self.cog.get_queue(self.guild_id)
            queue.insert(0, current)
            vc.stop()

    @discord.ui.button(emoji="⏸️", style=ButtonStyle.primary, custom_id="btn_pause", row=0)
    async def pause_button(self, interaction: Interaction, button: discord.ui.Button):
        """暫停 / 恢復播放"""
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ 機器人未連線！", ephemeral=True)

        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            button.style = ButtonStyle.success
            await interaction.response.edit_message(view=self)
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸️"
            button.style = ButtonStyle.primary
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("❌ 目前沒有正在播放的音樂！", ephemeral=True)

    @discord.ui.button(emoji="⏩", style=ButtonStyle.secondary, custom_id="btn_skip", row=0)
    async def skip_button(self, interaction: Interaction, button: discord.ui.Button):
        """跳過歌曲"""
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

    @discord.ui.button(emoji="🔀", style=ButtonStyle.secondary, custom_id="btn_shuffle", row=0)
    async def shuffle_button(self, interaction: Interaction, button: discord.ui.Button):
        """隨機打亂歌單"""
        queue = self.cog.get_queue(self.guild_id)
        if len(queue) < 2:
            return await interaction.response.send_message("❌ 佇列內歌曲太少，無法打亂！", ephemeral=True)
        
        random.shuffle(queue)
        await interaction.response.send_message("🔀 **已打亂當前播放佇列！**", delete_after=5)

    @discord.ui.button(emoji="⏹️", style=ButtonStyle.danger, custom_id="btn_stop", row=0)
    async def stop_button(self, interaction: Interaction, button: discord.ui.Button):
        """停止播放並退出"""
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        queue = self.cog.get_queue(self.guild_id)
        queue.clear()
        if self.guild_id in self.cog.current_track:
            self.cog.current_track.pop(self.guild_id)

        if vc:
            vc.stop()
            await vc.disconnect()
            
        await interaction.message.edit(view=None)


# ==========================================
# 🎵 音樂主核心 Cog
# ==========================================
class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_queues = {}
        self.current_track = {}
        self.control_messages = {}

    def get_queue(self, guild_id: int):
        if guild_id not in self.music_queues:
            self.music_queues[guild_id] = []
        return self.music_queues[guild_id]

    def create_progress_bar(self, duration: int) -> str:
        """建立極簡風格的進度條"""
        if duration == 0:
            return "🔴 直播中"
            
        m, s = divmod(duration, 60)
        h, m = divmod(m, 60)
        time_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        
        bar_length = 15
        filled_length = int(bar_length * 0.25)
        bar = "▬" * filled_length + "⚪" + "▬" * (bar_length - filled_length - 1)
        return f"00:00 {bar} {time_str}"

    def build_now_playing_embed(self, player, user_mention: str) -> discord.Embed:
        """建立中文化且不帶粉色調的高質感音樂卡片"""
        embed = discord.Embed(
            title="▶️ 正在播放：",
            description=f"**[{player.title}]({player.webpage_url})**\n\n🕒 **歌曲長度：** {self.create_progress_bar(player.duration)}\n👤 **點歌者：** {user_mention}",
            color=discord.Color.blurple()
        )
        if player.thumbnail:
            embed.set_thumbnail(url=player.thumbnail)
        
        embed.set_footer(text="🎵 夜櫻音樂系統 | 享受完美音質")
        return embed

    def play_next(self, guild_id: int, channel):
        """播放下一首歌曲"""
        vc = channel.guild.voice_client
        if not vc:
            return

        queue = self.get_queue(guild_id)
        if len(queue) > 0:
            next_song = queue.pop(0)
            self.current_track[guild_id] = next_song

            coro = YTDLSource.from_url(next_song['url'], loop=self.bot.loop, stream=True)
            future = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            
            try:
                player = future.result()
                vc.play(player, after=lambda e: self.play_next(guild_id, channel))
                
                embed = self.build_now_playing_embed(player, next_song['requester'])
                view = MusicControlView(self, guild_id)
                
                async def send_panel():
                    if guild_id in self.control_messages:
                        try:
                            await self.control_messages[guild_id].delete()
                        except:
                            pass
                    msg = await channel.send(embed=embed, view=view)
                    self.control_messages[guild_id] = msg
                
                asyncio.run_coroutine_threadsafe(send_panel(), self.bot.loop)

            except Exception as e:
                print(f"❌ 播放下一首時出錯: {e}")
                asyncio.run_coroutine_threadsafe(channel.send(f"❌ 播放歌曲 `{next_song['title']}` 失敗。自動播放下一首。"), self.bot.loop)
                self.play_next(guild_id, channel)
        else:
            if guild_id in self.current_track:
                self.current_track.pop(guild_id)
            asyncio.run_coroutine_threadsafe(self.auto_disconnect(channel.guild), self.bot.loop)

    async def auto_disconnect(self, guild):
        """歌單播完後，自動退出頻道"""
        await asyncio.sleep(180)
        vc = guild.voice_client
        if vc and not vc.is_playing() and len(self.get_queue(guild.id)) == 0:
            await vc.disconnect()
            if guild.id in self.control_messages:
                try:
                    await self.control_messages[guild.id].delete()
                except:
                    pass
                self.control_messages.pop(guild.id)

    @app_commands.command(name="play", description="播放或搜尋 YouTube 音樂，並呼叫中文化按鈕控制台")
    async def play(self, interaction: Interaction, url: str):
        await interaction.response.defer()

        if not interaction.user.voice:
            return await interaction.followup.send("❌ 您必須先加入語音頻道！", ephemeral=True)

        user_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client

        if not vc:
            vc = await user_channel.connect()
        elif vc.channel != user_channel:
            await vc.move_to(user_channel)

        guild_id = interaction.guild_id
        queue = self.get_queue(guild_id)

        try:
            loop = self.bot.loop or asyncio.get_event_loop()
            # 💡 這裡也全面採用手機 App 模擬安全解析通道！
            data = await safe_extract_info(loop, url, download=False, process=False)
            
            title = data.get('title', '未知歌曲')
            thumbnail = data.get('thumbnail')
            duration = data.get('duration', 0)
            webpage_url = data.get('webpage_url', url)
            
            song_info = {
                'url': url, 
                'title': title, 
                'thumbnail': thumbnail,
                'duration': duration,
                'webpage_url': webpage_url,
                'requester': interaction.user.mention
            }

            if vc.is_playing() or vc.is_paused():
                queue.append(song_info)
                
                queue_embed = discord.Embed(
                    title="📥 已成功加入排隊佇列",
                    description=f"**[{title}]({webpage_url})**",
                    color=discord.Color.blue()
                )
                if thumbnail:
                    queue_embed.set_thumbnail(url=thumbnail)
                queue_embed.set_footer(text=f"目前排在第 {len(queue)} 順位")
                
                await interaction.followup.send(embed=queue_embed)
            else:
                self.current_track[guild_id] = song_info
                player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
                vc.play(player, after=lambda e: self.play_next(guild_id, interaction.channel))
                
                embed = self.build_now_playing_embed(player, interaction.user.mention)
                view = MusicControlView(self, guild_id)
                
                msg = await interaction.followup.send(embed=embed, view=view)
                self.control_messages[guild_id] = msg

        except Exception as e:
            await interaction.followup.send(f"❌ 播放失敗，原因：`{e}`", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MusicCog(bot))