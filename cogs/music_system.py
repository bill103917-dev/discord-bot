import discord
from discord import app_commands, Interaction
from discord.ext import commands
import asyncio
import yt_dlp
import os

# ==========================================
# ⚙️ YouTube Cookie 安全載入機制
# ==========================================
COOKIE_FILE_PATH = None
YT_COOKIES_CONTENT = os.getenv("YT_COOKIES")

# 如果在環境變數中有找到 Cookie 內容，則寫入臨時檔案
if YT_COOKIES_CONTENT:
    COOKIE_FILE_PATH = "temp_youtube_cookies.txt"
    try:
        with open(COOKIE_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(YT_COOKIES_CONTENT)
        print("✅ [音樂系統] 已成功從環境變數載入 YouTube Cookie 檔案！")
    except Exception as e:
        print(f"❌ [音樂系統] 寫入 Cookie 檔案失敗: {e}")
        COOKIE_FILE_PATH = None

# ==========================================
# ⚙️ yt-dlp 與 FFmpeg 配置
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
    'source_address': '0.0.0.0',  # 綁定 IPv4
}

# 💡 如果有偵測到安全 Cookie，就將其注入 yt-dlp 設定中
if COOKIE_FILE_PATH:
    YTDL_FORMAT_OPTIONS['cookiefile'] = COOKIE_FILE_PATH

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# 建立預設的 yt-dlp 實體
ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        # 非同步解析 YouTube 網址
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            # 拿到的是播放清單，只取第一首
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.music_queues = {}  # 存放各伺服器的播放佇列 {guild_id: [songs]}

    def get_queue(self, guild_id: int):
        if guild_id not in self.music_queues:
            self.music_queues[guild_id] = []
        return self.music_queues[guild_id]

    def play_next(self, interaction: Interaction):
        """播放下一首歌曲"""
        guild_id = interaction.guild_id
        vc = interaction.guild.voice_client
        
        if not vc:
            return

        queue = self.get_queue(guild_id)
        if len(queue) > 0:
            next_song = queue.pop(0)
            
            # 使用協程非同步解析並播歌
            coro = YTDLSource.from_url(next_song['url'], loop=self.bot.loop, stream=True)
            future = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            
            try:
                player = future.result()
                vc.play(player, after=lambda e: self.play_next(interaction))
                
                # 發送目前播放提示
                asyncio.run_coroutine_threadsafe(
                    interaction.channel.send(f"🎵 **目前播放：** `{player.title}`"),
                    self.bot.loop
                )
            except Exception as e:
                print(f"❌ 播放下一首時出錯: {e}")
                self.play_next(interaction)
        else:
            # 沒歌了，5 分鐘後自動中斷連線
            asyncio.run_coroutine_threadsafe(self.auto_disconnect(interaction), self.bot.loop)

    async def auto_disconnect(self, interaction: Interaction):
        """若佇列空了，等待 5 分鐘後自動退出頻道"""
        await asyncio.sleep(300)
        vc = interaction.guild.voice_client
        if vc and not vc.is_playing() and len(self.get_queue(interaction.guild_id)) == 0:
            await vc.disconnect()
            await interaction.channel.send("🔇 歌曲已播放完畢，機器人已自動退出語音頻道。")

    @app_commands.command(name="play", description="播放 YouTube 音樂")
    async def play(self, interaction: Interaction, url: str):
        await interaction.response.defer()

        # 檢查使用者是否在語音頻道
        if not interaction.user.voice:
            return await interaction.followup.send("❌ 您必須先加入語音頻道！", ephemeral=True)

        user_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client

        # 連線至語音頻道
        if not vc:
            vc = await user_channel.connect()
        elif vc.channel != user_channel:
            await vc.move_to(user_channel)

        guild_id = interaction.guild_id
        queue = self.get_queue(guild_id)

        try:
            # 僅獲取影片標題資訊
            loop = self.bot.loop or asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False, process=False))
            
            title = data.get('title', '未知歌曲')
            song_info = {'url': url, 'title': title}

            if vc.is_playing() or vc.is_paused():
                queue.append(song_info)
                await interaction.followup.send(f"📥 **已加入佇列：** `{title}`")
            else:
                player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
                vc.play(player, after=lambda e: self.play_next(interaction))
                await interaction.followup.send(f"🎵 **開始播放：** `{player.title}`")

        except Exception as e:
            await interaction.followup.send(f"❌ 解析歌曲失敗，原因：`{e}`", ephemeral=True)

    @app_commands.command(name="skip", description="跳過當前歌曲")
    async def skip(self, interaction: Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.response.send_message("❌ 目前沒有正在播放的音樂！", ephemeral=True)

        await interaction.response.defer()
        vc.stop()  # 停止會自動觸發 after 呼叫 play_next
        await interaction.followup.send("⏩ **已跳過當前歌曲。**")

    @app_commands.command(name="stop", description="停止播放並清空歌單")
    async def stop(self, interaction: Interaction):
        vc = interaction.guild.voice_client
        guild_id = interaction.guild_id

        # 清空佇列
        if guild_id in self.music_queues:
            self.music_queues[guild_id].clear()

        if vc:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            await vc.disconnect()
            await interaction.response.send_message("🛑 **已停止播放，清空歌單並退出語音頻道。**")
        else:
            await interaction.response.send_message("❌ 機器人目前不在任何語音頻道中！", ephemeral=True)

    @app_commands.command(name="queue", description="顯示目前的播放歌單佇列")
    async def show_queue(self, interaction: Interaction):
        guild_id = interaction.guild_id
        queue = self.get_queue(guild_id)

        if not queue:
            return await interaction.response.send_message("📭 目前播放佇列是空的！")

        # 顯示前 10 首
        embed = discord.Embed(title="📋 當前播放佇列", color=discord.Color.blue())
        queue_text = ""
        for idx, song in enumerate(queue[:10], 1):
            queue_text += f"**{idx}.** `{song['title']}`\n"
        
        if len(queue) > 10:
            queue_text += f"\n*...以及其他 {len(queue) - 10} 首歌曲*"

        embed.description = queue_text
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(MusicCog(bot))