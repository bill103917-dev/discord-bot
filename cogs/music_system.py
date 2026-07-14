import discord
from discord import app_commands, Interaction, ButtonStyle
from discord.ext import commands
import asyncio
import yt_dlp
import os
import shutil
import traceback
import io
import random

# ==========================================
# ⚙️ 系統初始化與配置
# ==========================================
FFMPEG_PATH = shutil.which("ffmpeg")
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'default_search': 'scsearch:', # 強制使用 SoundCloud 搜尋
    'quiet': True,
    'no_warnings': True,
}
ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# ==========================================
# 🎛️ 音樂控制台按鈕介面 (恢復完整功能)
# ==========================================
class MusicControlView(discord.ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(emoji="⏸️", style=ButtonStyle.primary, custom_id="btn_pause")
    async def pause_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸️"
            await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="⏩", style=ButtonStyle.secondary, custom_id="btn_skip")
    async def skip_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        vc.stop()
        await interaction.response.send_message("⏭️ 已跳過歌曲", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=ButtonStyle.danger, custom_id="btn_stop")
    async def stop_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        self.cog.queues[self.guild_id] = []
        vc.stop()
        await vc.disconnect()
        await interaction.message.delete()

# ==========================================
# 🎵 音樂主核心 (恢復完整邏輯)
# ==========================================
class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}

    def get_queue(self, guild_id):
        if guild_id not in self.queues:
            self.queues[guild_id] = []
        return self.queues[guild_id]

    @app_commands.command(name="play", description="播放 Spotify 連結或歌曲名稱")
    async def play(self, interaction: Interaction, url: str):
        if "youtube.com" in url or "youtu.be" in url:
            return await interaction.response.send_message("❌ YouTube 連結目前受限。", ephemeral=True)
        
        await interaction.response.defer()
        
        # 強制關鍵字轉換，防止觸發 DRM
        query = f"scsearch:{url.split('/')[-1].split('?')[0]}" if "spotify" in url else url
        
        try:
            data = await asyncio.get_event_loop().run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
            song = data['entries'][0]
            
            queue = self.get_queue(interaction.guild_id)
            queue.append(song)
            
            if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
                await self.play_music(interaction)
            else:
                await interaction.followup.send(f"📥 已加入佇列: **{song['title']}**")
        except Exception as e:
            await interaction.followup.send(f"❌ 錯誤: {str(e)}")

    async def play_music(self, interaction):
        vc = await interaction.user.voice.channel.connect()
        while self.get_queue(interaction.guild_id):
            song = self.get_queue(interaction.guild_id).pop(0)
            player = discord.FFmpegPCMAudio(song['url'], executable=FFMPEG_PATH, before_options='-reconnect 1 -reconnect_streamed 1')
            vc.play(discord.PCMVolumeTransformer(player), after=lambda e: None)
            
            embed = discord.Embed(title="▶️ 正在播放", description=f"**{song['title']}**", color=discord.Color.blurple())
            await interaction.followup.send(embed=embed, view=MusicControlView(self, interaction.guild_id))
            
            while vc.is_playing() or vc.is_paused():
                await asyncio.sleep(1)

async def setup(bot):
    await bot.add_cog(MusicCog(bot))

