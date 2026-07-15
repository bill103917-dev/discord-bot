import discord
from discord import app_commands, Interaction, ButtonStyle
from discord.ext import commands, tasks
import asyncio
import aiohttp
import yt_dlp
import shutil
import time
import os
import re
import logging

log = logging.getLogger("MusicBot")
FFMPEG_PATH = shutil.which("ffmpeg")

# ==========================================
# ⚙️ yt-dlp 雙重配置與 Cookie 環境變數處理
# ==========================================
# 讀取與你之前設定完全一致的環境變數名稱：YT_COOKIES
YT_COOKIES_CONTENT = os.getenv("YT_COOKIES")
COOKIE_FILE_PATH = "temp_cookies.txt"

# 如果環境變數存在，我們動態寫入一個暫存的 cookie 檔案供 yt-dlp 使用
if YT_COOKIES_CONTENT:
    try:
        with open(COOKIE_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(YT_COOKIES_CONTENT)
        log.info("成功自環境變數 YT_COOKIES 載入 Cookie 設定。")
    except Exception as e:
        log.error(f"寫入暫存 Cookie 檔案失敗: {e}")
        COOKIE_FILE_PATH = None
else:
    COOKIE_FILE_PATH = None
    log.warning("未偵測到 YT_COOKIES 環境變數，YouTube 直接解析可能會受到限制，將自動啟用備用搜尋。")

# 1. 優先配置：使用動態產生的 Cookie 檔案進行直接解析
YTDL_DIRECT_OPTIONS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
}
if COOKIE_FILE_PATH:
    YTDL_DIRECT_OPTIONS['cookiefile'] = COOKIE_FILE_PATH

# 2. 備用配置：SoundCloud 多重搜尋（免 Cookie，防 IP 封鎖）
YTDL_SEARCH_OPTIONS = {
    'format': 'bestaudio/best',
    'default_search': 'scsearch5',  # 預設搜出 5 個最匹配結果
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'source_address': '0.0.0.0'
}

ytdl_direct = yt_dlp.YoutubeDL(YTDL_DIRECT_OPTIONS)
ytdl_search = yt_dlp.YoutubeDL(YTDL_SEARCH_OPTIONS)

# ==========================================
# 💾 伺服器播放狀態管理
# ==========================================
class GuildPlayState:
    def __init__(self):
        self.queue = []            # 播放佇列
        self.history = []          # 播放歷史紀錄
        self.current_song = None   # 當前正在播放的歌曲資訊
        self.control_message = None# 控制面板的 Message 物件
        self.lyrics_message = None # 歌詞面板的 Message 物件
        self.show_lyrics = True    # 是否預設開啟歌詞顯示
        self.parsed_lyrics = []    # 解析後的歌詞清單
        self.start_time = 0        # 歌曲開始播放的時間點 (timestamp)
        self.elapsed_time = 0      # 歌曲已播放的秒數
        self.paused = False        # 是否處於暫停狀態
        self.pause_start = 0       # 暫停開始的時間點 (timestamp)
        self.total_paused_sec = 0  # 累積暫停的秒數
        self.loop_mode = "off"     # "off", "single", "all"
        self.requester = None      # 點歌者
        self.volume = 0.5          # 預設音量 50%

# ==========================================
# 🎛️ 音樂多結果手動選擇選單 (Dropdown)
# ==========================================
class SongSelectView(discord.ui.View):
    def __init__(self, cog, songs: list, requester: discord.Member):
        super().__init__(timeout=60)
        self.cog = cog
        self.songs = songs
        self.requester = requester
        self.selected_song = None

        options = []
        for i, song in enumerate(songs[:5]):
            title = song.get('title', '未知音軌')[:90]
            artist = song.get('uploader') or song.get('artist') or '未知歌手'
            artist = artist[:90]
            
            options.append(discord.SelectOption(
                label=f"{i+1}. {title}",
                description=f"演唱/上傳者: {artist}",
                value=str(i)
            ))

        self.select = discord.ui.Select(
            placeholder="🕵️‍♂️ 偵查兵尋獲多個結果，請點擊挑選...",
            options=options
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: Interaction):
        if interaction.user.id != self.requester.id:
            return await interaction.response.send_message("❌ 只有點歌的人可以使用這個選單！", ephemeral=True)
        
        await interaction.response.defer()
        idx = int(self.select.values[0])
        self.selected_song = self.songs[idx]
        self.stop()

# ==========================================
# 🎛️ 音樂控制台按鈕介面
# ==========================================
class MusicControlView(discord.ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.update_button_states()

    def update_button_states(self):
        state = self.cog.get_state(self.guild_id)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "btn_play_pause":
                    if state.paused:
                        child.emoji = "▶️"
                        child.label = "繼續"
                        child.style = ButtonStyle.success
                    else:
                        child.emoji = "⏸️"
                        child.label = "暫停"
                        child.style = ButtonStyle.primary
                elif child.custom_id == "btn_loop":
                    if state.loop_mode == "off":
                        child.label = "循環: 關"
                        child.style = ButtonStyle.secondary
                    elif state.loop_mode == "single":
                        child.label = "循環: 單曲"
                        child.style = ButtonStyle.success
                    elif state.loop_mode == "all":
                        child.label = "循環: 佇列"
                        child.style = ButtonStyle.primary
                elif child.custom_id == "btn_previous":
                    child.disabled = len(state.history) == 0
                elif child.custom_id == "btn_lyrics_toggle":
                    if state.show_lyrics:
                        child.label = "歌詞: 顯示"
                        child.style = ButtonStyle.success
                    else:
                        child.label = "歌詞: 隱藏"
                        child.style = ButtonStyle.secondary

    @discord.ui.button(emoji="⏮️", label="上一首", style=ButtonStyle.secondary, custom_id="btn_previous", row=0)
    async def previous_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        state = self.cog.get_state(self.guild_id)

        if not vc or len(state.history) == 0:
            return await interaction.followup.send("❌ 沒有播放歷史紀錄，無法播放上一首。", ephemeral=True)

        if state.current_song:
            state.queue.insert(0, state.current_song)
            
        prev_song = state.history.pop()
        state.current_song = prev_song

        state.paused = False
        vc.stop()
        
        await self.cog.play_audio_stream(vc, interaction, state.current_song, start_sec=0)
        
        self.update_button_states()
        embed = self.cog.create_playing_embed(self.guild_id)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send("⏮️ 已切換回上一首歌曲", ephemeral=True)

    @discord.ui.button(emoji="◀️", label="後退 30s", style=ButtonStyle.secondary, custom_id="btn_rewind", row=0)
    async def rewind_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.followup.send("❌ 目前沒有正在播放的歌曲，無法後退。", ephemeral=True)

        state = self.cog.get_state(self.guild_id)
        if state.paused:
            current_elapsed = state.pause_start - state.start_time - state.total_paused_sec
        else:
            current_elapsed = time.time() - state.start_time - state.total_paused_sec

        target_time = max(0, int(current_elapsed - 30))

        vc.stop()
        await self.cog.play_audio_stream(vc, interaction, state.current_song, start_sec=target_time)
        await interaction.followup.send(f"◀️ 已成功後退 30 秒 (跳至 {target_time // 60:02d}:{target_time % 60:02d})", ephemeral=True)

    @discord.ui.button(emoji="⏸️", label="暫停", style=ButtonStyle.primary, custom_id="btn_play_pause", row=0)
    async def play_pause_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ 機器人目前不在語音頻道中。", ephemeral=True)

        state = self.cog.get_state(self.guild_id)
        if vc.is_playing() and not state.paused:
            vc.pause()
            state.paused = True
            state.pause_start = time.time()
            await interaction.response.send_message("⏸️ 播放已暫停", ephemeral=True)
        elif vc.is_paused() and state.paused:
            vc.resume()
            state.paused = False
            state.total_paused_sec += time.time() - state.pause_start
            await interaction.response.send_message("▶️ 播放已繼續", ephemeral=True)

        self.update_button_states()
        embed = self.cog.create_playing_embed(self.guild_id)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(emoji="▶️", label="快進 30s", style=ButtonStyle.secondary, custom_id="btn_fast_forward", row=0)
    async def fast_forward_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.followup.send("❌ 目前沒有正在播放的歌曲，無法快進。", ephemeral=True)

        state = self.cog.get_state(self.guild_id)
        if state.paused:
            current_elapsed = state.pause_start - state.start_time - state.total_paused_sec
        else:
            current_elapsed = time.time() - state.start_time - state.total_paused_sec

        target_time = int(current_elapsed + 30)

        if target_time >= state.current_song['duration']:
            vc.stop()
            return await interaction.followup.send("⏩ 快進已超出歌曲長度，直接播放下一首！", ephemeral=True)

        vc.stop()
        await self.cog.play_audio_stream(vc, interaction, state.current_song, start_sec=target_time)
        await interaction.followup.send(f"▶️ 已成功快進 30 秒 (跳至 {target_time // 60:02d}:{target_time % 60:02d})", ephemeral=True)

    @discord.ui.button(emoji="⏭️", label="跳過", style=ButtonStyle.secondary, custom_id="btn_skip", row=0)
    async def skip_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            state = self.cog.get_state(self.guild_id)
            if state.loop_mode == "single":
                state.loop_mode = "off"
            
            if state.current_song:
                state.history.append(state.current_song)
                
            vc.stop()
            await interaction.response.send_message("⏭️ 已成功跳過當前歌曲", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 目前沒有正在播放的音樂。", ephemeral=True)

    # --- 第二排按鈕：音量、歌詞與系統功能 ---

    @discord.ui.button(emoji="🔉", label="小聲 10%", style=ButtonStyle.secondary, custom_id="btn_vol_down", row=1)
    async def vol_down_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ 機器人目前不在語音頻道中。", ephemeral=True)

        state = self.cog.get_state(self.guild_id)
        state.volume = max(0.0, state.volume - 0.1)
        if vc.source:
            vc.source.volume = state.volume

        embed = self.cog.create_playing_embed(self.guild_id)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"🔉 音量已降低至 {int(state.volume * 100)}%", ephemeral=True)

    @discord.ui.button(emoji="🔊", label="大聲 10%", style=ButtonStyle.secondary, custom_id="btn_vol_up", row=1)
    async def vol_up_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ 機器人目前不在語音頻道中。", ephemeral=True)

        state = self.cog.get_state(self.guild_id)
        state.volume = min(2.0, state.volume + 0.1)
        if vc.source:
            vc.source.volume = state.volume

        embed = self.cog.create_playing_embed(self.guild_id)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"🔊 音量已提高至 {int(state.volume * 100)}%", ephemeral=True)

    @discord.ui.button(emoji="🎤", label="歌詞: 顯示", style=ButtonStyle.success, custom_id="btn_lyrics_toggle", row=1)
    async def lyrics_toggle_button(self, interaction: Interaction, button: discord.ui.Button):
        state = self.cog.get_state(self.guild_id)
        state.show_lyrics = not state.show_lyrics
        
        if not state.show_lyrics and state.lyrics_message:
            try:
                await state.lyrics_message.delete()
            except Exception:
                pass
            state.lyrics_message = None
            await interaction.response.send_message("🎤 已關閉動態歌詞顯示", ephemeral=True)
        else:
            await interaction.response.send_message("🎤 已開啟動態歌詞，將於下一秒自動輸出面板", ephemeral=True)
            
        self.update_button_states()
        embed = self.cog.create_playing_embed(self.guild_id)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(emoji="🔁", label="循環: 關", style=ButtonStyle.secondary, custom_id="btn_loop", row=1)
    async def loop_button(self, interaction: Interaction, button: discord.ui.Button):
        state = self.cog.get_state(self.guild_id)
        if state.loop_mode == "off":
            state.loop_mode = "single"
            await interaction.response.send_message("🔂 已切換為「單曲循環」模式", ephemeral=True)
        elif state.loop_mode == "single":
            state.loop_mode = "all"
            await interaction.response.send_message("🔁 已切換為「佇列循環」模式", ephemeral=True)
        else:
            state.loop_mode = "off"
            await interaction.response.send_message("➡️ 已關閉循環模式", ephemeral=True)

        self.update_button_states()
        embed = self.cog.create_playing_embed(self.guild_id)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(emoji="⏹️", label="停止", style=ButtonStyle.danger, custom_id="btn_stop", row=1)
    async def stop_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        await self.cog.cleanup_and_disconnect(self.guild_id, vc)
        await interaction.response.send_message("⏹️ 已停止播放並關閉面板。", ephemeral=True)

# ==========================================
# 🎵 音樂主核心 Cog
# ==========================================
class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.states = {}
        self.ui_update_loop.start()

    def cog_unload(self):
        self.ui_update_loop.cancel()
        # 清理動態生成的暫存 cookie 檔案
        if os.path.exists(COOKIE_FILE_PATH):
            try:
                os.remove(COOKIE_FILE_PATH)
            except Exception:
                pass

    def get_state(self, guild_id: int) -> GuildPlayState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildPlayState()
        return self.states[guild_id]

    async def fetch_synced_lyrics(self, title: str) -> list:
        clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', title).strip()
        url = f"https://lrclib.net/api/search?q={clean_title}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and isinstance(data, list):
                            for entry in data:
                                if entry.get('syncedLyrics'):
                                    return self.parse_lrc_text(entry['syncedLyrics'])
        except Exception as e:
            log.error(f"獲取歌詞 API 失敗: {e}")
        return []

    def parse_lrc_text(self, lrc_content: str) -> list:
        parsed = []
        lines = lrc_content.split('\n')
        for line in lines:
            match = re.match(r'\[(\d+):(\d+(?:\.\d+)?)]\s*(.*)', line)
            if match:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                total_seconds = minutes * 60 + seconds
                text = match.group(3).strip()
                if text:
                    parsed.append((total_seconds, text))
        parsed.sort(key=lambda x: x[0])
        return parsed

    def get_current_lyric_lines(self, guild_id: int) -> str:
        state = self.get_state(guild_id)
        if not state.parsed_lyrics:
            return "🎤 暫時無法獲取這首歌的動態歌詞..."

        elapsed = state.elapsed_time
        current_idx = 0
        
        for i, (lyric_time, _) in enumerate(state.parsed_lyrics):
            if elapsed >= lyric_time:
                current_idx = i
            else:
                break

        prev_line = f"*{state.parsed_lyrics[current_idx - 1][1]}*" if current_idx > 0 else "---"
        current_line = f"**👉 {state.parsed_lyrics[current_idx][1]}**"
        next_line = f"*{state.parsed_lyrics[current_idx + 1][1]}*" if current_idx < len(state.parsed_lyrics) - 1 else "---"

        return f"{prev_line}\n\n{current_line}\n\n{next_line}"

    async def cleanup_and_disconnect(self, guild_id: int, vc):
        state = self.get_state(guild_id)
        state.queue.clear()
        state.history.clear()
        state.current_song = None
        
        if state.control_message:
            try:
                await state.control_message.delete()
            except Exception:
                pass
            state.control_message = None

        if state.lyrics_message:
            try:
                await state.lyrics_message.delete()
            except Exception:
                pass
            state.lyrics_message = None

        if vc:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            if vc.is_connected():
                await vc.disconnect()

    def create_progress_bar(self, elapsed, duration):
        if duration == 0:
            return "🔴 直播中 [▬▬▬▬▬▬▬▬▬▬▬▬▬▬]"
        
        bar_length = 15
        progress = int((elapsed / duration) * bar_length)
        progress = max(0, min(bar_length, progress))
        
        bar = ""
        for i in range(bar_length):
            if i == progress:
                bar += "🔘"
            else:
                bar += "▬"
        
        elapsed_m, elapsed_s = divmod(int(elapsed), 60)
        dur_m, dur_s = divmod(int(duration), 60)
        
        return f"`{elapsed_m:02d}:{elapsed_s:02d} / {dur_m:02d}:{dur_s:02d}`\n{bar}"

    def create_playing_embed(self, guild_id: int) -> discord.Embed:
        state = self.get_state(guild_id)
        song = state.current_song
        if not song:
            return discord.Embed(title="⏹️ 目前未播放音樂", color=discord.Color.dark_gray())

        if state.paused:
            state.elapsed_time = state.pause_start - state.start_time - state.total_paused_sec
        else:
            state.elapsed_time = time.time() - state.start_time - state.total_paused_sec

        state.elapsed_time = max(0, min(song['duration'], state.elapsed_time))

        embed = discord.Embed(
            title="🎧 正在播放",
            description=f"**[{song['title']}]({song['webpage_url']})**",
            color=discord.Color.blurple() if not state.paused else discord.Color.dark_gray()
        )
        
        embed.add_field(name="📼 播放進度", value=self.create_progress_bar(state.elapsed_time, song['duration']), inline=False)
        
        loop_status = "關閉" if state.loop_mode == "off" else ("單曲 🔂" if state.loop_mode == "single" else "整個佇列 🔁")
        
        artist_display = song['artist'] if song['artist'] else "⚠️ 此首歌沒有演唱者資訊"
        
        embed.add_field(name="👤 點歌者", value=state.requester.mention if state.requester else "未知", inline=True)
        embed.add_field(name="🎤 演唱者 / 上傳者", value=artist_display, inline=True)
        embed.add_field(name="🔄 循環模式", value=loop_status, inline=True)
        embed.add_field(name="🔊 音量大小", value=f"{int(state.volume * 100)}%", inline=True)
        embed.add_field(name="🎵 佇列剩餘", value=f"{len(state.queue)} 首歌曲", inline=True)
        
        embed.set_footer(text="數據源：SoundCloud • 已啟用雙重決策環境變數Cookie技術")
        if song.get('thumbnail'):
            embed.set_thumbnail(url=song['thumbnail'])
            
        return embed

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        vc = member.guild.voice_client
        if not vc:
            return
        if vc.channel and len(vc.channel.members) == 1:
            await asyncio.sleep(5)
            if vc.channel and len(vc.channel.members) == 1:
                await self.cleanup_and_disconnect(member.guild.id, vc)

    @tasks.loop(seconds=2)
    async def ui_update_loop(self):
        for guild_id, state in self.states.items():
            if state.current_song:
                vc = self.bot.get_guild(guild_id).voice_client
                if vc and (vc.is_playing() or vc.is_paused()):
                    try:
                        if state.control_message:
                            embed = self.create_playing_embed(guild_id)
                            view = MusicControlView(self, guild_id)
                            await state.control_message.edit(embed=embed, view=view)
                        
                        if state.show_lyrics:
                            lyric_embed = discord.Embed(
                                title="🎤 歌詞同步面板 (動態滾動)",
                                description=self.get_current_lyric_lines(guild_id),
                                color=discord.Color.green()
                            )
                            lyric_embed.set_footer(text=f"歌曲：{state.current_song['title']}")
                            
                            if not state.lyrics_message and state.control_message:
                                state.lyrics_message = await state.control_message.channel.send(embed=lyric_embed)
                            elif state.lyrics_message:
                                await state.lyrics_message.edit(embed=lyric_embed)
                    except Exception:
                        pass

    @ui_update_loop.before_loop
    async def before_ui_update_loop(self):
        await self.bot.wait_until_ready()

    # =======================================================
    # 🕵️‍♂️ 雙重解析偵查模組 (Cookie 環境變數 + SoundCloud 搜尋)
    # =======================================================
    async def scout_music_dual_mode(self, query: str, interaction: Interaction) -> dict:
        loop = asyncio.get_event_loop()

        # A. 如果是直接連結，優先嘗試「環境變數 Cookie 直接解析」
        if "youtube.com" in query or "youtu.be" in query or "soundcloud.com" in query:
            try:
                log.info("偵查兵嘗試使用環境變數 Cookie 直接解析音軌...")
                data = await loop.run_in_executor(None, lambda: ytdl_direct.extract_info(query, download=False))
                
                if 'entries' in data and len(data['entries']) > 0:
                    song_data = data['entries'][0]
                else:
                    song_data = data

                title = song_data.get('title', '⚠️ 此影片沒有標題資訊')
                artist = song_data.get('uploader') or song_data.get('artist') or song_data.get('creator')
                
                log.info(f"直接解析成功：{title}")
                return {
                    'title': title,
                    'artist': artist if artist else None,
                    'url': song_data.get('url'),
                    'duration': song_data.get('duration', 0),
                    'webpage_url': song_data.get('webpage_url', query),
                    'thumbnail': song_data.get('thumbnail', None)
                }

            except Exception as e:
                # ❗️ 直接解析失敗，啟動第二計畫：利用無阻礙 API 抓取 Metadata 並進行 SoundCloud 備用搜尋
                log.warning(f"直接解析失敗 ({e})。偵查兵啟動 SoundCloud 備用重定向手動選歌計畫！")
                await interaction.followup.send("⚠️ 原始連結遭平台阻擋或 Cookie 失效。正在為您搜尋備用音源...", ephemeral=True)
                
                title, artist = await self.get_video_metadata_safely(query)
                search_keyword = f"{title} {artist if artist else ''}"
                return await self.search_and_select_song(search_keyword, interaction)

        # B. 如果輸入的是一般關鍵字，直接進行手動搜尋與選取
        else:
            return await self.search_and_select_song(query, interaction)

    async def get_video_metadata_safely(self, url: str) -> tuple:
        """透過無阻擋的官方 API 獲取影片基礎標題"""
        if "youtube.com" in url or "youtu.be" in url:
            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(oembed_url, timeout=5) as response:
                        if response.status == 200:
                            yt_info = await response.json()
                            return yt_info.get('title', '未知標題'), yt_info.get('author_name', '')
            except Exception:
                pass
        return url, ""

    async def search_and_select_song(self, keyword: str, interaction: Interaction) -> dict:
        """在 SoundCloud 搜尋 5 首並彈出選取選單供使用者決定"""
        loop = asyncio.get_event_loop()
        search_query = f"scsearch5:{keyword}"
        
        data = await loop.run_in_executor(None, lambda: ytdl_search.extract_info(search_query, download=False))
        
        if 'entries' not in data or len(data['entries']) == 0:
            raise Exception("偵查兵在網路上也搜不到任何匹配的歌曲！")

        entries = data['entries']
        
        if len(entries) == 1:
            song_data = entries[0]
            artist = song_data.get('uploader') or song_data.get('artist') or song_data.get('creator')
            return {
                'title': song_data.get('title', '未知音軌'),
                'artist': artist if artist else None,
                'url': song_data.get('url'),
                'duration': song_data.get('duration', 0),
                'webpage_url': song_data.get('webpage_url', ''),
                'thumbnail': song_data.get('thumbnail', None)
            }

        select_view = SongSelectView(self, entries, interaction.user)
        menu_msg = await interaction.followup.send(
            content="🔍 偵查兵尋獲了多個匹配的備用音源，請選擇你要播放的版本：",
            view=select_view,
            ephemeral=True
        )

        await select_view.wait()

        try:
            await menu_msg.delete()
        except Exception:
            pass

        if select_view.selected_song is None:
            raise Exception("❌ 超時未選擇歌曲，已取消點歌流程。")

        chosen = select_view.selected_song
        artist = chosen.get('uploader') or chosen.get('artist') or chosen.get('creator')
        return {
            'title': chosen.get('title', '未知音軌'),
            'artist': artist if artist else None,
            'url': chosen.get('url'),
            'duration': chosen.get('duration', 0),
            'webpage_url': chosen.get('webpage_url', ''),
            'thumbnail': chosen.get('thumbnail', None)
        }

    @app_commands.command(name="play", description="播放音樂 (雙重 Cookie 解析 + SoundCloud 多結果手動選取)")
    async def play(self, interaction: Interaction, query: str):
        # 1. 進入規則優先檢查
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ 你必須先加入一個語音頻道，才能使用播放指令！", ephemeral=True)
        
        await interaction.response.defer()

        voice_channel = interaction.user.voice.channel
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        state.requester = interaction.user

        await interaction.followup.send("🕵️‍♂️ 專屬偵查兵正在搜尋與分析音軌中，請稍候...", ephemeral=True)

        try:
            song = await self.scout_music_dual_mode(query, interaction)
        except Exception as e:
            log.error(f"解析音源失敗: {e}")
            return await interaction.followup.send(f"❌ 點歌失敗：{str(e)}")

        # 儲存到佇列
        state.queue.append(song)
        vc = interaction.guild.voice_client
        if not vc:
            vc = await voice_channel.connect()
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)

        if not vc.is_playing() and not vc.is_paused():
            await self.play_next(interaction, vc)
        else:
            await interaction.followup.send(f"📥 已成功排入隊列：**{song['title']}**")

    async def play_audio_stream(self, vc, interaction, song, start_sec=0):
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)

        ffmpeg_options = {
            'before_options': f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss {start_sec}',
            'options': '-vn'
        }

        audio_source = discord.FFmpegPCMAudio(
            song['url'],
            executable=FFMPEG_PATH,
            **ffmpeg_options
        )
        transformer = discord.PCMVolumeTransformer(audio_source, volume=state.volume)

        def after_playing(error):
            if error:
                log.error(f"FFmpeg 錯誤: {error}")
            self.bot.loop.create_task(self.handle_song_end(interaction, vc))

        vc.play(transformer, after=after_playing)

        state.start_time = time.time() - start_sec
        state.total_paused_sec = 0
        state.paused = False

    async def play_next(self, interaction: Interaction, vc):
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)

        if len(state.queue) == 0:
            await self.cleanup_and_disconnect(guild_id, vc)
            return

        if state.current_song:
            state.history.append(state.current_song)

        state.current_song = state.queue.pop(0)
        
        state.parsed_lyrics = []
        if state.lyrics_message:
            try:
                await state.lyrics_message.delete()
            except Exception:
                pass
            state.lyrics_message = None
            
        asyncio.create_task(self.load_lyrics_for_song(state.current_song['title'], guild_id))

        await self.play_audio_stream(vc, interaction, state.current_song, start_sec=0)

        embed = self.create_playing_embed(guild_id)
        view = MusicControlView(self, guild_id)
        
        if state.control_message:
            try:
                await state.control_message.delete()
            except Exception:
                pass

        msg = await interaction.channel.send(embed=embed, view=view)
        state.control_message = msg

    async def load_lyrics_for_song(self, song_title: str, guild_id: int):
        state = self.get_state(guild_id)
        lyrics = await self.fetch_synced_lyrics(song_title)
        state.parsed_lyrics = lyrics

    async def handle_song_end(self, interaction: Interaction, vc):
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)

        if not vc or not vc.is_connected():
            return
        if vc.is_playing():
            return

        if state.loop_mode == "single" and state.current_song:
            await self.play_audio_stream(vc, interaction, state.current_song, start_sec=0)
            embed = self.create_playing_embed(guild_id)
            view = MusicControlView(self, guild_id)
            if state.control_message:
                await state.control_message.edit(embed=embed, view=view)
            return

        if state.loop_mode == "all" and state.current_song:
            state.queue.append(state.current_song)

        await self.play_next(interaction, vc)

async def setup(bot):
    await bot.add_cog(MusicCog(bot))