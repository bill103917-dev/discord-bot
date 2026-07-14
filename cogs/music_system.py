import discord
from discord import app_commands, Interaction, ButtonStyle
from discord.ext import commands
import asyncio
import yt_dlp
import os
import random
import shutil
import traceback

# ==========================================
# 🔍 系統相依性檢查 (自動尋找 ffmpeg)
# ==========================================
FFMPEG_PATH = shutil.which("ffmpeg")
if FFMPEG_PATH:
    print(f"✅ [音樂系統] 成功在系統路徑找到 FFmpeg: {FFMPEG_PATH}")
else:
    print("❌ [音樂系統 警告] 在系統中找不到 FFmpeg！機器人將無法播放任何語音！")

# ==========================================
# ⚙️ YouTube Cookie 安全載入機制
# ==========================================
COOKIE_FILE_PATH = None
YT_COOKIES_CONTENT = os.getenv("YT_COOKIES")

if YT_COOKIES_CONTENT:
    COOKIE_FILE_PATH = "temp_youtube_cookies.txt"
    try:
        with open(COOKIE_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(YT_COOKIES_CONTENT.strip())
        print("✅ [音樂系統] 已成功從環境變數載入 YouTube Cookie 檔案！")
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
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'android'],
        }
    },
}

FALLBACK_FORMAT_OPTIONS = YTDL_FORMAT_OPTIONS.copy()
if 'format' in FALLBACK_FORMAT_OPTIONS:
    del FALLBACK_FORMAT_OPTIONS['format']

if COOKIE_FILE_PATH:
    YTDL_FORMAT_OPTIONS['cookiefile'] = COOKIE_FILE_PATH
    FALLBACK_FORMAT_OPTIONS['cookiefile'] = COOKIE_FILE_PATH

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 1048576',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)
ytdl_fallback = yt_dlp.YoutubeDL(FALLBACK_FORMAT_OPTIONS)


# ==========================================
# 🛡️ 雙重安全解析輔助函式
# ==========================================
async def safe_extract_info(loop, url, download=False, process=False):
    def _extract():
        try:
            return ytdl.extract_info(url, download=download, process=process)
        except Exception as e:
            err_str = str(e)
            if "Requested format is not available" in err_str or "format" in err_str.lower():
                print(f"⚠️ [音樂系統] 格式受限，啟動『無限制格式』備用解析協定...")
                return ytdl_fallback.extract_info(url, download=download, process=process)
            raise e

    return await loop.run_in_executor(None, _extract)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', '未知歌曲')
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
        
        ffmpeg_exe = FFMPEG_PATH if FFMPEG_PATH else "ffmpeg"
        return cls(discord.FFmpegPCMAudio(filename, executable=ffmpeg_exe, **FFMPEG_OPTIONS), data=data)


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
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and self.cog.current_track.get(self.guild_id):
            current = self.cog.current_track[self.guild_id]
            queue = self.cog.get_queue(self.guild_id)
            queue.insert(0, current)
            vc.stop()

    @discord.ui.button(emoji="⏸️", style=ButtonStyle.primary, custom_id="btn_pause", row=0)
    async def pause_button(self, interaction: Interaction, button: discord.ui.Button):
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
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

    @discord.ui.button(emoji="🔀", style=ButtonStyle.secondary, custom_id="btn_shuffle", row=0)
    async def shuffle_button(self, interaction: Interaction, button: discord.ui.Button):
        queue = self.cog.get_queue(self.guild_id)
        if len(queue) < 2:
            return await interaction.response.send_message("❌ 佇列內歌曲太少，無法打亂！", ephemeral=True)
        
        random.shuffle(queue)
        await interaction.response.send_message("🔀 **已打亂當前播放佇列！**", delete_after=5)

    @discord.ui.button(emoji="⏹️", style=ButtonStyle.danger, custom_id="btn_stop", row=0)
    async def stop_button(self, interaction: Interaction, button: discord.ui.Button):
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
        vc = channel.guild.voice_client
        if not vc:
            return

        queue = self.get_queue(guild_id)
        if len(queue) > 0:
            next_song = queue.pop(0)
            self.current_track[guild_id] = next_song

            coro = YTDLSource.from_url(next_song['url'], loop=self.bot.loop, stream=True)
            future = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            
            # 💡 動態字串拼接定義，防範 markdown 溢出問題
            ticks = "```"

            try:
                player = future.result()
                
                def after_playing_callback(error):
                    if error:
                        print(f"❌ [音樂系統] 播放中途發生錯誤: {error}")
                        err_embed = discord.Embed(
                            title="⚠️ 播放中途因錯誤中斷",
                            description=f"歌曲: `{next_song['title']}`\n\n**詳細錯誤成因：**\n{ticks}text\n{str(error)[:1500]}\n{ticks}",
                            color=discord.Color.red()
                        )
                        asyncio.run_coroutine_threadsafe(channel.send(embed=err_embed), self.bot.loop)
                    
                    self.play_next(guild_id, channel)

                vc.play(player, after=after_playing_callback)
                
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
                print(f"❌ [音樂系統] 播放加載失敗: {e}")
                tb_str = traceback.format_exc()
                
                err_embed = discord.Embed(
                    title="❌ 歌曲播放失敗 (加載階段)",
                    description=f"無法加載並播送歌曲：`{next_song['title']}`\n\n**詳細錯誤日誌：**\n{ticks}text\n{tb_str[:1500]}\n{ticks}",
                    color=discord.Color.red()
                )
                err_embed.set_footer(text="💡 提示：如果出現 403 Forbidden，請確認並更新 YT_COOKIES 環境變數")
                
                asyncio.run_coroutine_threadsafe(channel.send(embed=err_embed), self.bot.loop)
                self.play_next(guild_id, channel)
        else:
            if guild_id in self.current_track:
                self.current_track.pop(guild_id)
            asyncio.run_coroutine_threadsafe(self.auto_disconnect(channel.guild), self.bot.loop)

    async def auto_disconnect(self, guild):
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

        # 💡 動態字串拼接定義，防範 markdown 溢出問題
        ticks = "```"

        try:
            loop = self.bot.loop or asyncio.get_event_loop()
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
                
                def after_playing_callback(error):
                    if error:
                        print(f"❌ [音樂系統] 播放首首歌中途發生錯誤: {error}")
                        err_embed = discord.Embed(
                            title="⚠️ 播放中途因錯誤中斷",
                            description=f"歌曲: `{title}`\n\n**詳細錯誤成因：**\n{ticks}text\n{str(error)[:1500]}\n{ticks}",
                            color=discord.Color.red()
                        )
                        asyncio.run_coroutine_threadsafe(interaction.channel.send(embed=err_embed), self.bot.loop)
                    self.play_next(guild_id, interaction.channel)

                vc.play(player, after=after_playing_callback)
                
                embed = self.build_now_playing_embed(player, interaction.user.mention)
                view = MusicControlView(self, guild_id)
                
                msg = await interaction.followup.send(embed=embed, view=view)
                self.control_messages[guild_id] = msg

        except Exception as e:
            tb_str = traceback.format_exc()
            err_embed = discord.Embed(
                title="❌ 指令解析失敗",
                description=f"無法解析此網址：`{url}`\n\n**錯誤原因：**\n{ticks}text\n{tb_str[:1500]}\n{ticks}",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=err_embed, ephemeral=False)


async def setup(bot):
    await bot.add_cog(MusicCog(bot))