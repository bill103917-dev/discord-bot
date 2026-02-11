# ------------------------------
# Helper：安全取得 VoiceClient
# ------------------------------
async def get_voice_client(interaction: Interaction) -> Optional[discord.VoiceClient]:
    if not interaction.guild:
        await interaction.followup.send("❌ 這個指令只能在伺服器中使用。", ephemeral=True)
        return None
    return interaction.guild.voice_client

# ------------------------------
# End-of-Queue view (詢問是否離開)
# ------------------------------
class EndOfQueueView(discord.ui.View):
    def __init__(self, cog, guild_id: int, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="繼續留在頻道", style=discord.ButtonStyle.primary)
    async def keep_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ 機器人會保留在語音頻道。使用 /play 加入新歌曲。", ephemeral=True)
        # 刪除提示訊息（由使用者決定）
        try:
            await interaction.message.delete()
        except Exception:
            pass

    @discord.ui.button(label="離開語音頻道", style=discord.ButtonStyle.danger)
    async def leave_button(self, interaction: Interaction, button: discord.ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc and vc.is_connected():
            await vc.disconnect()
        self.cog.vc_dict.pop(self.guild_id, None)
        self.cog.queue.pop(self.guild_id, None)
        self.cog.now_playing.pop(self.guild_id, None)
        self.cog.current_volume.pop(self.guild_id, None)
        await interaction.response.send_message("👋 機器人已離開語音頻道。", ephemeral=True)
        try:
            await interaction.message.delete()
        except Exception:
            pass

# ------------------------------
# MusicControlView: 主控制面板（含按鈕）
# ------------------------------
class MusicControlView(discord.ui.View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

# ===============================================
# 📌 修改 2：MusicControlView 的 interaction_check
# (位於第二段程式碼中 MusicControlView 類別內部)
# ===============================================
# ===============================================
# 📌 修正：修正關鍵字大小寫與縮排
# ===============================================
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        vc = self.cog.vc_dict.get(self.guild_id)
        
        # 1. 如果機器人沒連線，僅限管理員操作
        if not vc or not vc.is_connected():
            return interaction.user.guild_permissions.administrator
        
        # 2. 檢查使用者是否在同一個語音頻道
        if interaction.user.voice and interaction.user.voice.channel == vc.channel:
            return True
            
        # 3. 若不在頻道，檢查是否為管理員
        if interaction.user.guild_permissions.administrator:
            return True

        # 4. 以上條件都不符合，報錯並攔截
        await interaction.response.send_message("❌ 你必須與機器人在同一個語音頻道才能控制音樂！", ephemeral=True)
        return False


        
    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.primary)
    async def btn_pause_resume(self, interaction: Interaction, button: discord.ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if not vc:
            return await interaction.response.send_message("❌ 機器人不在語音頻道", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ 已暫停", ephemeral=True)
        else:
            vc.resume()
            await interaction.response.send_message("▶️ 已繼續", ephemeral=True)
        await self.cog.update_control_message(self.guild_id)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def btn_skip(self, interaction: Interaction, button: discord.ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if not vc:
            return await interaction.response.send_message("❌ 機器人不在語音頻道", ephemeral=True)
        vc.stop()
        await interaction.response.send_message("⏩ 已跳過", ephemeral=True)

    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.danger)
    async def btn_stop(self, interaction: Interaction, button: discord.ui.Button):
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc:
            vc.stop()
            await vc.disconnect()
        self.cog.queue.pop(self.guild_id, None)
        self.cog.now_playing.pop(self.guild_id, None)
        self.cog.current_volume.pop(self.guild_id, None)
        self.cog.vc_dict.pop(self.guild_id, None)
        await interaction.response.send_message("⏹️ 已停止並離開語音頻道", ephemeral=True)
        await self.cog.update_control_message(self.guild_id)

    @discord.ui.button(label="🔊 +", style=discord.ButtonStyle.success)
    async def btn_vol_up(self, interaction: Interaction, button: discord.ui.Button):
        gid = self.guild_id
        vc = self.cog.vc_dict.get(gid)
        if not vc:
            return await interaction.response.send_message("❌ 機器人不在語音頻道", ephemeral=True)
        new = min(1.0, self.cog.current_volume.get(gid, 0.5) + 0.1)
        self.cog.current_volume[gid] = new
        if vc.source:
            vc.source.volume = new
        await interaction.response.send_message(f"🔊 音量 {int(new*100)}%", ephemeral=True)
        await self.cog.update_control_message(gid)

    @discord.ui.button(label="🔇 -", style=discord.ButtonStyle.danger)
    async def btn_vol_down(self, interaction: Interaction, button: discord.ui.Button):
        gid = self.guild_id
        vc = self.cog.vc_dict.get(gid)
        if not vc:
            return await interaction.response.send_message("❌ 機器人不在語音頻道", ephemeral=True)
        new = max(0.0, self.cog.current_volume.get(gid, 0.5) - 0.1)
        self.cog.current_volume[gid] = new
        if vc.source:
            vc.source.volume = new
        await interaction.response.send_message(f"🔇 音量 {int(new*100)}%", ephemeral=True)
        await self.cog.update_control_message(gid)

# ------------------------------
# VoiceCog
# ------------------------------
class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue = {}             # {guild_id: [(audio_url, title, duration, thumb, webpage_url), ...]}
        self.now_playing = {}       # {guild_id: (title, duration, start_time, thumb, webpage_url)}
        self.vc_dict = {}           # {guild_id: voice_client}
        self.current_volume = {}    # {guild_id: float}
        self.control_messages = {}  # {guild_id: message_id}

        # 只在啟動時從環境變數讀取 cookies（YOUTUBE_COOKIES）
        cookie_data = os.getenv("YOUTUBE_COOKIES")
        self.cookies_file = None
        if cookie_data:
            tf = tempfile.NamedTemporaryFile(delete=False, prefix="yt_cookies_", suffix=".txt")
            tf.write(cookie_data.encode("utf-8"))
            tf.flush()
            tf.close()
            self.cookies_file = tf.name
            print(f"✅ YOUTUBE_COOKIES 載入到暫存檔: {self.cookies_file}")
        else:
            print("⚠️ 未發現環境變數 YOUTUBE_COOKIES，部分影片可能無法播放")

    def cog_unload(self):
        # 清理暫存 cookies 檔（如果存在）
        try:
            if self.cookies_file and os.path.exists(self.cookies_file):
                os.remove(self.cookies_file)
        except Exception:
            pass

    # --------------------
    # 使用 yt-dlp 提取音訊（支援搜尋/連結 + cookies）
    # 回傳：audio_url, title, duration (秒), thumbnail, webpage_url
    # --------------------
    async def extract_audio(self, query: str):
        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "default_search": "ytsearch1",
            "nocheckcertificate": True,
        }
        if self.cookies_file:
            ydl_opts["cookiefile"] = self.cookies_file

        try:
            # run in thread (yt-dlp is blocking)
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: YoutubeDL(ydl_opts).extract_info(query, download=False))
            if "entries" in info:
                info = info["entries"][0]
            audio_url = info.get("url")
            title = info.get("title", "未知曲目")
            duration = info.get("duration", 0) or 0
            thumb = info.get("thumbnail")
            webpage_url = info.get("webpage_url") or info.get("id")
            return audio_url, title, int(duration), thumb, webpage_url
        except Exception as e:
            # 回傳 None 表示失敗，呼叫端會處理（包含自動退出語音頻道）
            print(f"❌ extract_audio 錯誤: {e}")
            return None, None, 0, None, None

    # --------------------
    # 播放器啟動（播放隊首）
    # --------------------
    async def start_playback(self, guild_id: int):
        vc = self.vc_dict.get(guild_id)
        if not vc:
            return
        q = self.queue.get(guild_id, [])
        if not q:
            return

        audio_url, title, duration, thumb, webpage_url = q.pop(0)
        self.now_playing[guild_id] = (title, duration, asyncio.get_event_loop().time(), thumb, webpage_url)
        volume = self.current_volume.setdefault(guild_id, 0.5)

        source = FFmpegPCMAudio(
            audio_url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn"
        )
        source = discord.PCMVolumeTransformer(source, volume)

        def _after(error):
            # run the coroutine in bot loop
            fut = asyncio.run_coroutine_threadsafe(self.player_after_callback(guild_id, error), self.bot.loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass

        try:
            vc.play(source, after=_after)
        except Exception as e:
            # 播放失敗 -> 通知並斷開
            print(f"❌ 播放時例外: {e}")
            await self._handle_play_error(guild_id, str(e))
            return

        # update control message
        await self.update_control_message(guild_id)

    async def _handle_play_error(self, guild_id: int, error_text: str):
        # 若發生播放/提取錯誤，自動斷開並告知
        vc = self.vc_dict.get(guild_id)
        if vc and vc.is_connected():
            try:
                await vc.disconnect()
            except Exception:
                pass
        self.vc_dict.pop(guild_id, None)
        self.queue.pop(guild_id, None)
        self.now_playing.pop(guild_id, None)
        self.current_volume.pop(guild_id, None)
        self.control_messages.pop(guild_id, None)
        # 嘗試找到一個文字頻道可發送錯誤
        # 優先使用剛剛的控制訊息頻道或 guild 第一文字頻道
        try:
            guild = self.bot.get_guild(guild_id)
            if guild:
                ch = None
                # 嘗試取先前控制訊息的頻道
                if guild.text_channels:
                    ch = guild.text_channels[0]
                if ch:
                    await ch.send(f"❌ 播放發生錯誤並已離開語音頻道：{error_text}")
        except Exception:
            pass

    # --------------------
    # 播放結束 / after callback
    # --------------------
    async def player_after_callback(self, guild_id: int, error):
        if error:
            print(f"播放時發生錯誤: {error}")
            # 如果 error，是 yt-dlp 的提取或播放錯誤，則直接斷開
            await self._handle_play_error(guild_id, str(error))
            return

        # 清除目前播放資訊
        self.now_playing.pop(guild_id, None)

        # 如果隊列還有歌則接著播放
        if self.queue.get(guild_id):
            await self.start_playback(guild_id)
            return

        # 隊列已空：不自動離開；發送詢問訊息（大圖 + 縮圖）
        # 找可發訊息的頻道
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            ch = None
            # 嘗試從 vc 取得頻道
            vc = self.vc_dict.get(guild_id)
            if vc and vc.channel and vc.channel.guild and vc.channel.guild.text_channels:
                ch = vc.channel.guild.text_channels[0]
            if not ch and guild.text_channels:
                ch = guild.text_channels[0]
            if not ch:
                return

            # build embed with large image + thumbnail (thumbnail as embed.thumbnail, large image as embed.set_image)
            embed = discord.Embed(title="🎶 隊列已播放完畢", description="所有歌曲已播放完成。你要讓機器人離開語音頻道，還是繼續等待/加入新歌曲？", color=discord.Color.blurple())
            # show some helpful hint
            embed.add_field(name="操作提示", value="按下按鈕選擇：繼續留在頻道 或 離開語音頻道\n你也可以直接使用 `/play <關鍵字或連結>` 繼續播放。", inline=False)

            # use last thumbnail if available
            last_thumb = None
            last_webpage = None
            if self.now_playing.get(guild_id) is None:
                # we popped current, but we might recall last queued thumb from nowhere,
                # alternatively use control_messages stored info - for simplicity, try to use last queued thumbnail variable (not reliable).
                pass

            # For nicer UI, attempt to use the last known thumbnail from control message (if any)
            # We stored thumb in now_playing; but since it's popped, attempt to use a fallback:
            # if queue was empty, we can't get thumb now; so skip image if none.

            # Send view
            view = EndOfQueueView(self, guild_id)
            await ch.send(embed=embed, view=view)
        except Exception as e:
            print(f"player_after_callback 發送完畢訊息失敗: {e}")

    # --------------------
    # 更新控制訊息（會顯示嵌入含縮圖與大圖）
    # --------------------
    async def update_control_message(self, guild_id: int, channel: discord.TextChannel = None):
        vc = self.vc_dict.get(guild_id)
        q = self.queue.get(guild_id, [])
        now = self.now_playing.get(guild_id)  # (title, duration, start_time, thumb, webpage)
        view = MusicControlView(self, guild_id)

        # 決定要發在哪個文字頻道
        target_channel = channel
        if not target_channel and vc and vc.channel and vc.channel.guild and vc.channel.guild.text_channels:
            target_channel = vc.channel.guild.text_channels[0]
        if not target_channel:
            return

        embed = discord.Embed(title="🎶 音樂播放器", color=discord.Color.blue())
        status = "目前無播放"
        if vc and vc.is_playing():
            status = "▶️ 播放中"
        elif vc and vc.is_paused():
            status = "⏸️ 已暫停"
        elif vc and not vc.is_playing() and q:
            status = "🔃 即將播放"
        embed.add_field(name="狀態", value=status, inline=False)

        if now:
            title, duration, start_ts, thumb, webpage = now
            vol_percent = int(self.current_volume.get(guild_id, 0.5) * 100)
            embed.add_field(name="現在播放", value=f"**{title}**\n`{duration}s` (音量: {vol_percent}%)", inline=False)
            # set thumbnail (left small) and image (big)
            if thumb:
                embed.set_thumbnail(url=thumb)
                embed.set_image(url=thumb)  # both: big and thumbnail (some clients show both)
            if webpage:
                embed.add_field(name="連結", value=f"[開啟影片]({webpage})", inline=False)
        else:
            embed.add_field(name="現在播放", value="無", inline=False)

        if q:
            queue_text = "\n".join([f"{i+1}. {item[1]} (`{item[2]}s`)" for i, item in enumerate(q[:10])])
            embed.add_field(name=f"即將播放 ({len(q)} 首)", value=queue_text, inline=False)
        else:
            embed.add_field(name="隊列", value="隊列是空的", inline=False)

        try:
            msg_id = self.control_messages.get(guild_id)
            if msg_id:
                try:
                    msg = await target_channel.fetch_message(msg_id)
                    await msg.edit(embed=embed, view=view)
                    return
                except discord.NotFound:
                    pass
            # send new message
            msg = await target_channel.send(embed=embed, view=view)
            self.control_messages[guild_id] = msg.id
        except Exception as e:
            print(f"更新控制訊息失敗: {e}")

    # --------------------
    # Slash commands
    # --------------------
    @app_commands.command(name="play", description="播放 YouTube 音樂或搜尋歌曲")
    @app_commands.describe(query="歌曲連結或關鍵字")
    async def play(self, interaction: Interaction, query: str):
        await interaction.response.defer(ephemeral=False)
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("❌ 你必須先加入語音頻道", ephemeral=True)

        guild_id = interaction.guild.id
        channel = interaction.user.voice.channel

        # connect or move
        vc = interaction.guild.voice_client
        just_connected = False
        if not vc:
            vc = await channel.connect()
            just_connected = True
        elif vc.channel != channel:
            await vc.move_to(channel)
        self.vc_dict[guild_id] = vc
        self.current_volume.setdefault(guild_id, 0.5)

        # extract audio (in thread)
        audio_url, title, duration, thumb, webpage = await self.extract_audio(query)
        if not audio_url:
            # 如果剛連接且沒有取得音訊 -> 斷開以避免佔用
            if just_connected:
                try:
                    await vc.disconnect()
                except Exception:
                    pass
                self.vc_dict.pop(guild_id, None)
            return await interaction.followup.send("❌ 取得音訊失敗，可能需要有效的 YOUTUBE_COOKIES 或該影片受限。", ephemeral=True)

        # push to queue
        self.queue.setdefault(guild_id, []).append((audio_url, title, duration, thumb, webpage))
        await self.update_control_message(guild_id, interaction.channel)

        # start playback if not playing
        if not vc.is_playing() and not vc.is_paused():
            asyncio.create_task(self.start_playback(guild_id))

        await interaction.followup.send(f"✅ **{title}** 已加入隊列！", ephemeral=True)

    @app_commands.command(name="skip", description="跳過目前歌曲")
    async def skip(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        vc = interaction.guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            return await interaction.followup.send("❌ 目前沒有播放中的音樂。", ephemeral=True)
        skipped = self.now_playing.get(interaction.guild.id, ("當前歌曲", 0))[0]
        vc.stop()
        await interaction.followup.send(f"⏩ 已跳過 **{skipped}**。", ephemeral=True)

    @app_commands.command(name="pause", description="暫停播放")
    async def pause(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.followup.send("❌ 目前沒有播放中的音樂。", ephemeral=True)
        vc.pause()
        await interaction.followup.send("⏸️ 已暫停。", ephemeral=True)
        await self.update_control_message(interaction.guild.id)

    @app_commands.command(name="resume", description="繼續播放")
    async def resume(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused():
            return await interaction.followup.send("❌ 目前沒有暫停的音樂。", ephemeral=True)
        vc.resume()
        await interaction.followup.send("▶️ 已繼續。", ephemeral=True)
        await self.update_control_message(interaction.guild.id)

    @app_commands.command(name="stop", description="停止並離開語音頻道")
    async def stop(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        guild_id = interaction.guild.id
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            vc.stop()
            try:
                await vc.disconnect()
            except Exception:
                pass
            self.vc_dict.pop(guild_id, None)
        self.queue.pop(guild_id, None)
        self.now_playing.pop(guild_id, None)
        self.current_volume.pop(guild_id, None)
        await interaction.followup.send("⏹️ 已停止並離開語音頻道。", ephemeral=True)
        await self.update_control_message(guild_id)

    @app_commands.command(name="queue", description="查看播放隊列")
    async def queue_cmd(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        gid = interaction.guild.id
        q = self.queue.get(gid, [])
        if not q:
            return await interaction.followup.send("📭 隊列是空的", ephemeral=True)
        text = "**🎶 播放隊列：**\n" + "\n".join([f"{i+1}. {item[1]} (`{item[2]}s`)" for i, item in enumerate(q[:25])])
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="np", description="顯示正在播放的歌曲")
    async def np_cmd(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=False)
        now = self.now_playing.get(interaction.guild.id)
        if not now:
            return await interaction.followup.send("❌ 目前沒有在播放", ephemeral=True)
        title, duration, start_ts, thumb, webpage = now
        await interaction.followup.send(f"🎧 現在播放：**{title}** (`{duration}s`)\n{webpage}", ephemeral=True)

    @app_commands.command(name="volume", description="設置音量 (0-100)")
    async def volume_cmd(self, interaction: Interaction, level: app_commands.Range[int, 0, 100]):
        await interaction.response.defer(ephemeral=False)
        gid = interaction.guild.id
        vol = level / 100.0
        self.current_volume[gid] = vol
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = vol
        await interaction.followup.send(f"🔊 音量已設為 {level}%", ephemeral=True)
        await self.update_control_message(gid)