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
else:
    print("⚠️ [音樂系統 警告] 未偵測到 YT_COOKIES 環境變數！")

# ==========================================
# ⚙️ yt-dlp 與 FFmpeg 終極安全配置
# ==========================================
YTDL_FORMAT_OPTIONS = {
    # 採用寬鬆的音軌優先格式，若無純音軌則自動相容複合格式
    'format': 'bestaudio/best/ba/b',  
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,  # 設為 True 避免中途解析直接拋異常中斷
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    # 🚀 終極混淆協議：混合使用行動裝置與創作者端 API 繞過 PoToken 驗證
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web_creator', 'mweb', 'ios'],
            'skip': ['webpage', 'hls', 'dash'],
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    }
}

# 備用配置：完全放開限制
FALLBACK_FORMAT_OPTIONS = YTDL_FORMAT_OPTIONS.copy()
FALLBACK_FORMAT_OPTIONS['format'] = 'best'

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
# 🛡️ 雙重安全解析輔助函式 (自動降級與空值防護)
# ==========================================
async def safe_extract_info(loop, url, download=False, process=False):
    """
    安全解析 YouTube 影片資訊，具備強大的空值與錯誤防護機制。
    """
    def _extract():
        try:
            data = ytdl.extract_info(url, download=download, process=process)
            if data is None:
                raise ValueError("YouTube 伺服器拒絕連線，回傳了空數據 (通常為 IP 被封鎖)。")
            return data
        except Exception as e:
            print(f"⚠️ [音樂系統] 首次解析遭遇困難，正啟動極致降級解析... 原因: {e}")
            try:
                data = ytdl_fallback.extract_info(url, download=download, process=process)
                if data is None:
                    raise ValueError("降級解析後依然無法取得資料。")
                return data
            except Exception as fallback_error:
                # 結合兩次錯誤拋出，方便診斷
                raise RuntimeError(
                    f"首輪解析錯誤: {e}\n次輪降級錯誤: {fallback_error}\n"
                    f"💡 診斷提示: 這通常代表 Render IP 被封鎖。請務必在 Render 設定有效且新鮮的 'YT_COOKIES' 環境變數。"
                )

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
        
        embed.set_footer(text="🎵 音樂系統 | 享受完美音質")
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
                err_embed.set_footer(text="💡 提示：此狀況極高機率為 Render IP 被 YouTube 封鎖。請更新您的 YT_COOKIES 環境變數！")
                
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
                description=f"無法解析此網址：`{url}`\n\n**錯誤原因：**\n`{ticks}text\n{tb_str[:1500]}\n{ticks}`",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=err_embed, ephemeral=False)


async def setup(bot):
    await bot.add_cog(MusicCog(bot))