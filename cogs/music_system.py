import discord
from discord.ext import commands
from typing import Optional
from utils.yt import YT
from utils.queueSys import music_queue, channelQueue
from cogs.utils import next_song
from utils.logger import setup_logger

log = setup_logger(__name__)

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.yt = YT()

    @discord.app_commands.command(name="play", description="播放一首歌")
    async def play(self, ctx: discord.Interaction, song: str, channel: Optional[discord.VoiceChannel] = None):
        await ctx.response.defer()
        
        # 取得語音頻道
        voice_channel = channel or (ctx.user.voice.channel if ctx.user.voice else None)
        if not voice_channel:
            return await ctx.followup.send("❌ 你必須先進入語音頻道！")

        # 搜尋音樂
        videos = self.yt.search(song, max_results=1)
        if not videos:
            return await ctx.followup.send(f"❌ 找不到關於 '{song}' 的結果")

        video = videos[0]
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)

        # 連線邏輯
        if not voice:
            voice = await voice_channel.connect()
        elif voice.channel != voice_channel:
            await voice.move_to(voice_channel)

        if isinstance(voice, discord.StageChannel):
            await voice.guild.me.edit(suppress=False)

        # 隊列處理
        if voice not in music_queue:
            music_queue[voice] = channelQueue(None, ctx)

        if voice.is_playing():
            music_queue[voice].add(video)
            await ctx.followup.send(f"➕ 已加入隊列: **{video.title}**")
            return

        # 播放邏輯
        try:
            audio = self.yt.stream(video.id)
            if not audio:
                return await ctx.followup.send("❌ 無法獲取音訊串流。")
            
            music_queue[voice].set_current(audio)
            await ctx.followup.send(f"▶️ 正在播放: **{video.title}**")
            
            voice.play(
                music_queue[voice].audio,
                after=lambda e: self.bot.loop.create_task(next_song(ctx)) if not e else log.error(f"播放錯誤: {e}")
            )
        except Exception as e:
            log.error(f"播放時發生未預期錯誤: {e}")
            await ctx.followup.send("❌ 播放過程中發生錯誤。")

    @play.autocomplete("song")
    async def song_autocomplete(self, ctx: discord.Interaction, current: str):
        if not current: return []
        videos = self.yt.search(current, max_results=10)
        return [discord.app_commands.Choice(name=v.title, value=v.url) for v in videos]

    @discord.app_commands.command(name="playlist", description="播放播放清單連結")
    async def playlist(self, ctx: discord.Interaction, url: str, channel: Optional[discord.VoiceChannel] = None, shuffle: bool = False):
        await ctx.response.defer()
        
        voice_channel = channel or (ctx.user.voice.channel if ctx.user.voice else None)
        if not voice_channel:
            return await ctx.followup.send("❌ 你必須先進入語音頻道！")

        result = self.yt.get_playlist_videos(url)
        if not result or not result[1]:
            return await ctx.followup.send("❌ 無法解析該播放清單。")

        title, videos = result
        voice = discord.utils.get(self.bot.voice_clients, guild=ctx.guild) or await voice_channel.connect()
        
        if voice not in music_queue:
            music_queue[voice] = channelQueue(None, ctx)

        for video in videos:
            music_queue[voice].add(video)

        if shuffle: music_queue[voice].shuffle()
        
        await ctx.followup.send(f"🎵 已加入播放清單: **{title}** ({len(videos)} 首)")

        if not voice.is_playing():
            await self._trigger_next(voice, ctx)

    async def _trigger_next(self, voice, ctx):
        next_video = music_queue[voice].next()
        if next_video:
            audio = self.yt.stream(next_video.id)
            music_queue[voice].set_current(audio)
            voice.play(music_queue[voice].audio, after=lambda e: self.bot.loop.create_task(next_song(ctx)))

async def setup(bot):
    await bot.add_cog(Music(bot))

