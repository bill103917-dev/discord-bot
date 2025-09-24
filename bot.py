import os
import sys
import datetime
import threading
import asyncio
import re
import random
import requests
import spotipy
import yt_dlp
from typing import List, Optional

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction, TextChannel, User, Message, FFmpegPCMAudio
from flask import Flask, session, request, render_template, redirect, url_for


# =========================
# ⚡ 環境變數和常數設定
# =========================
# 從環境變數中讀取密碼和 OAuth2 資訊
TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

if not TOKEN:
    print("❌ DISCORD_TOKEN 沒有正確設定，請到環境變數檢查！")
    sys.exit(1)
if not all([DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI]):
    print("❌ 缺少必要的 Discord OAuth2 環境變數，請檢查！")
    sys.exit(1)

# 特殊使用者列表（替換成你的 Discord ID）
SPECIAL_USER_IDS = [1238436456041676853]
# 暫存指令紀錄，只保留最近100筆
command_logs = []


# =========================
# ⚡ Discord 機器人設定
# =========================
intents = discord.Intents.default()
intents.members = True # 這裡對應後台的 SERVER MEMBERS INTENT
intents.message_content = True # 如果需要讀取訊息內容則開啟
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# ⚡ Flask 網頁管理後台設定
# =========================
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# OAuth2 相關 URL
DISCORD_API_BASE_URL = "https://discord.com/api/v10"
AUTH_URL = f"{DISCORD_API_BASE_URL}/oauth2/authorize?response_type=code&client_id={DISCORD_CLIENT_ID}&scope=identify%20guilds&redirect_uri={DISCORD_REDIRECT_URI}"
TOKEN_URL = f"{DISCORD_API_BASE_URL}/oauth2/token"
USER_URL = f"{DISCORD_API_BASE_URL}/users/@me"


# =========================
# ⚡ 通用函式
# =========================
async def log_command(interaction, command_name):
    """紀錄指令使用，以供網頁後台顯示"""
    guild_name = interaction.guild.name if interaction.guild else "私人訊息"
    channel_name = interaction.channel.name if interaction.channel else "未知頻道"
    log_text = f"📝 {interaction.user} 在伺服器「{guild_name}」的頻道「#{channel_name}」使用了 {command_name}"
    command_logs.append({
        "text": log_text,
        "time": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    })
    if len(command_logs) > 100:
        command_logs.pop(0)

# =========================
# ⚡ 指令相關類別和 Cog
# =========================


# 剪刀石頭布參數
active_games = {}

class RPSInviteView(discord.ui.View):
    def __init__(self, challenger, opponent, rounds):
        super().__init__(timeout=30)
        self.challenger = challenger
        self.opponent = opponent
        self.rounds = rounds
        self.value = None

    def make_invite_embed(self):
        return discord.Embed(
            title="🎮 剪刀石頭布挑戰",
            description=f"{self.challenger.mention} 邀請 {self.opponent.mention} 進行剪刀石頭布 (搶 {self.rounds} 勝)\n\n請選擇是否接受！",
            color=discord.Color.blurple()
        )

    @discord.ui.button(label="✅ 接受", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("❌ 只有被邀請的人可以按！", ephemeral=True)
            return
        self.value = True
        await interaction.response.edit_message(content=f"{self.opponent.mention} 接受了挑戰！", embed=None, view=None)
        self.stop()

    @discord.ui.button(label="❌ 拒絕", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("❌ 只有被邀請的人可以按！", ephemeral=True)
            return
        self.value = False
        await interaction.response.edit_message(content=f"{self.opponent.mention} 拒絕了挑戰。", embed=None, view=None)
        self.stop()


class RPSView(discord.ui.View):
    def __init__(self, player1, player2=None, rounds=3, vs_bot=False):
        super().__init__(timeout=60)
        self.player1 = player1
        self.player2 = player2
        self.vs_bot = vs_bot
        self.rounds = rounds
        self.current_round = 1
        self.scores = {player1: 0}
        if player2:
            self.scores[player2] = 0
        elif vs_bot:
            self.scores["bot"] = 0
        self.choices = {}
        if vs_bot:
            self.choices["bot"] = random.choice(["✊", "✌️", "✋"])  # 機器人先出拳
        self.message = None
        active_games[player1.id] = self

    def make_embed(self, game_over=False, winner=None, round_result=None):
        title = f"🎮 剪刀石頭布 - 第 {self.current_round} 回合 / 搶 {self.rounds} 勝"
        p1_score = self.scores.get(self.player1, 0)
        p2_score = self.scores.get(self.player2, 0) if self.player2 else self.scores.get("bot", 0)

        desc = f"🏆 **比分**：{self.player1.mention} **{p1_score}** - **{p2_score}** {self.player2.mention if self.player2 else '🤖 機器人'}\n\n"
        if game_over:
            desc += f"🎉 **{winner}** 獲勝！"
        elif round_result:
            desc += round_result + "\n\n請繼續選擇你的出拳：✊ / ✌️ / ✋"
        else:
            desc += "請選擇你的出拳：✊ / ✌️ / ✋"
        return discord.Embed(title=title, description=desc, color=discord.Color.blurple())

    def make_cancel_embed(self):
        return discord.Embed(title="🛑 遊戲已取消", description="這場比賽已被取消。", color=discord.Color.red())

    def make_timeout_embed(self):
        return discord.Embed(title="⌛ 遊戲超時", description="60 秒內沒有出拳，判定認輸。", color=discord.Color.orange())

    async def handle_round(self):
        p1_choice = self.choices.get(self.player1)
        p2_choice = self.choices.get(self.player2) if self.player2 else self.choices.get("bot")

        result_msg = f"🎮 第 {self.current_round} 回合\n"
        result_msg += f"{self.player1.mention} 出了 {p1_choice} ✅\n"
        result_msg += f"{self.player2.mention if self.player2 else '🤖 機器人'} 出了 {p2_choice} ✅"
        await self.message.edit(embed=self.make_embed(), content=result_msg)

        await asyncio.sleep(1)

        if p1_choice == p2_choice:
            round_result = "🤝 這回合平手！"
        elif (p1_choice, p2_choice) in [("✌️", "✋"), ("✊", "✌️"), ("✋", "✊")]:
            round_result = f"✅ {self.player1.mention} 贏了這回合！"
            self.scores[self.player2 if self.player2 else "bot"] += 1
        else:
            round_result = f"✅ {self.player2.mention if self.player2 else '🤖 機器人'} 贏了這回合！"
            self.scores[self.player2 if self.player2 else "bot"] += 1

        if self.scores[self.player1] >= self.rounds or self.scores[self.player2 if self.player2 else "bot"] >= self.rounds:
            winner = self.player1.mention if self.scores[self.player1] > self.scores[self.player2 if self.player2 else "bot"] else (self.player2.mention if self.player2 else "🤖 機器人")
            await self.message.edit(embed=self.make_embed(game_over=True, winner=winner), content=None, view=None)
            active_games.pop(self.player1.id, None)
            self.stop()
        else:
            self.current_round += 1
            self.choices.clear()
            if self.vs_bot:
                self.choices["bot"] = random.choice(["✊", "✌️", "✋"])
            await self.message.edit(embed=self.make_embed(round_result=round_result), content=None, view=self)

    async def on_timeout(self):
        await self.message.edit(embed=self.make_timeout_embed(), view=None, content=None)
        active_games.pop(self.player1.id, None)
        self.stop()

    @discord.ui.button(label="✊", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.make_choice(interaction, "✊")

    @discord.ui.button(label="✌️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.make_choice(interaction, "✌️")

    @discord.ui.button(label="✋", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.make_choice(interaction, "✋")

    @discord.ui.button(label="❌ 取消遊戲", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.player1:
            await interaction.response.send_message("❌ 只有主辦方可以取消遊戲！", ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.make_cancel_embed(), view=None, content=None)
        active_games.pop(self.player1.id, None)
        self.stop()

    async def make_choice(self, interaction: discord.Interaction, choice: str):
        if interaction.user not in [self.player1, self.player2] and not self.vs_bot:
            await interaction.response.send_message("❌ 你不是參加玩家！", ephemeral=True)
            return
        if interaction.user in self.choices:
            await interaction.response.send_message("❌ 你已經出過拳了！", ephemeral=True)
            return
        self.choices[interaction.user] = choice
        await interaction.response.defer()

        expected = 2 if not self.vs_bot else 1
        if len(self.choices) >= expected:
            await self.handle_round()


# =========================
# ⚡ 指令 Cogs
# =========================
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    # =====================
    # /say 指令
    # =====================
    
    @app_commands.command(name="say", description="讓機器人發送訊息（管理員或特殊使用者限定）")
    async def say(self, interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None, user: Optional[discord.User] = None):
        await log_command(interaction, "/say")

        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if user:
            try:
                await user.send(message)
                await interaction.followup.send(f"✅ 已私訊給 {user.mention}", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 發送失敗: {e}", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        try:
            await target_channel.send(message)
            await interaction.followup.send(f"✅ 已在 {target_channel.mention} 發送訊息", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 發送失敗: {e}", ephemeral=True)

    @app_commands.command(name="announce", description="發布公告（管理員限定）")
    async def announce(self, interaction: discord.Interaction, content: str, title: Optional[str] = "公告📣", channel: Optional[discord.TextChannel] = None, ping_everyone: bool = False):
        await log_command(interaction, "/announce")
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("❌ 只有管理員能發布公告", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        embed = discord.Embed(
            title=title,
            description=content,
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"發布者：{interaction.user.display_name}")

        mention = "@everyone" if ping_everyone else ""
        await target_channel.send(content=mention, embed=embed)
        await interaction.followup.send(f"✅ 公告已發送到 {target_channel.mention}", ephemeral=True)

    @app_commands.command(name="calc", description="簡單計算器")
    async def calc(self, interaction: discord.Interaction, expr: str):
        await log_command(interaction, "/calc")
        try:
            allowed = "0123456789+-*/(). "
            if not all(c in allowed for c in expr):
                raise ValueError("包含非法字符")
            result = eval(expr)
            await interaction.response.send_message(f"結果：{result}")
        except Exception as e:
            await interaction.response.send_message(f"計算錯誤：{e}")

    @app_commands.command(name="delete", description="刪除訊息（管理員限定）")
    async def delete(self, interaction: discord.Interaction, amount: int):
        await log_command(interaction, "/delete")
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 只有管理員可以刪除訊息", ephemeral=True)
            return
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ 請輸入 1 ~ 100 的數字", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount+1)
            await interaction.followup.send(f"✅ 已刪除 {len(deleted)-1} 則訊息", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 刪除失敗: {e}", ephemeral=True)


class ReactionRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reaction_roles = {}

    @app_commands.command(name="reactionrole", description="新增反應身分組（管理員用）")
    async def reactionrole(self, interaction: discord.Interaction, message: str, emoji: str, role: discord.Role, channel: Optional[discord.TextChannel] = None):
        await log_command(interaction, "/reactionrole")
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有管理員可以使用此指令", ephemeral=True)
            return

        msg_obj = None
        if re.match(r"https?://", message):
            try:
                m = re.match(r"https?://discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", message)
                if not m:
                    await interaction.response.send_message("❌ 訊息連結格式錯誤", ephemeral=True)
                    return
                guild_id, channel_id, message_id = map(int, m.groups())
                channel_obj = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                msg_obj = await channel_obj.fetch_message(message_id)
            except Exception as e:
                await interaction.response.send_message(f"❌ 無法解析訊息連結: {e}", ephemeral=True)
                return
        else:
            if channel is None:
                channel = interaction.channel
            async for msg in channel.history(limit=100):
                if msg.content == message:
                    msg_obj = msg
                    break
            if msg_obj is None:
                await interaction.response.send_message("❌ 找不到符合的訊息", ephemeral=True)
                return

        try:
            await msg_obj.add_reaction(emoji)
        except Exception as e:
            await interaction.response.send_message(f"❌ 無法加反應: {e}", ephemeral=True)
            return

        guild_roles = self.reaction_roles.setdefault(interaction.guild_id, {})
        msg_roles = guild_roles.setdefault(msg_obj.id, {})
        msg_roles[emoji] = role.id
        await interaction.response.send_message(f"✅ 已設定 {emoji} -> {role.name}", ephemeral=True)


class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="rps", description="剪刀石頭布對戰")
    async def rps(self, interaction: discord.Interaction, rounds: int = 3, opponent: Optional[discord.User] = None, vs_bot: bool = False):
        await log_command(interaction, "/rps")
        if not opponent and not vs_bot:
            await interaction.response.send_message("❌ 你必須選擇對手或開啟 vs_bot!", ephemeral=True)
            return
        if opponent and opponent.bot:
            await interaction.response.send_message("🤖 不能邀請機器人，請改用 vs_bot=True", ephemeral=True)
            return

        if opponent:
            await interaction.response.defer()
            invite_view = RPSInviteView(interaction.user, opponent, rounds)
            msg = await interaction.followup.send(embed=invite_view.make_invite_embed(), view=invite_view)
            await invite_view.wait()
            if invite_view.value is None:
                await msg.edit(content=f"{opponent.mention} 沒有回應，挑戰取消。", embed=None, view=None)
                return
            if not invite_view.value:
                return

        view = RPSView(interaction.user, opponent, rounds, vs_bot)
        embed = view.make_embed()
        view.message = await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="dice", description="擲一顆 1-6 的骰子")
    async def dice(self, interaction: discord.Interaction):
        await log_command(interaction, "/dice")
        number = random.randint(1, 6)
        await interaction.response.send_message(f"🎲 {interaction.user.mention} 擲出了 **{number}**！")


class PingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="測試機器人是否在線")
    async def ping(self, interaction: discord.Interaction):
        await log_command(interaction, "/ping")
        await interaction.response.send_message(f"🏓 Pong! {round(bot.latency*1000)}ms")


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="顯示所有可用的指令")
    async def help(self, interaction: discord.Interaction):
        await log_command(interaction, "/help")
        embed = discord.Embed(title="📖 指令清單", description="以下是目前可用的指令：", color=discord.Color.blue())
        for cmd in self.bot.tree.get_commands():
            embed.add_field(name=f"/{cmd.name}", value=cmd.description or "沒有描述", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


import discord
from discord.ext import commands
from discord import app_commands
from discord import FFmpegPCMAudio
import yt_dlp
import asyncio

class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # 每個 guild 的播放隊列
        self.now_playing = {}  # 正在播放曲目
        self.vc_dict = {}  # 儲存語音客戶端

    @app_commands.command(name="play", description="播放 YouTube 音樂")
    async def play(self, interaction: discord.Interaction, url: str):
        # 確認使用者在語音頻道
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ 你必須先加入語音頻道", ephemeral=True)
            return
        channel = interaction.user.voice.channel

        # 連接語音頻道
        vc = interaction.guild.voice_client
        if not vc:
            vc = await channel.connect()
        elif vc.channel != channel:
            await vc.move_to(channel)
        self.vc_dict[interaction.guild.id] = vc

        # 取得 YouTube 音訊
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'noplaylist': True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if 'entries' in info:  # playlist
                    info = info['entries'][0]
                audio_url = info['url']
                title = info.get('title', '未知曲目')
        except Exception as e:
            await interaction.response.send_message(f"❌ 取得音訊失敗: {e}", ephemeral=True)
            return

        # 加入播放隊列
        q = self.queue.setdefault(interaction.guild.id, [])
        q.append((audio_url, title))

        # 建立嵌入消息
        embed = discord.Embed(
            title="🎵 正在播放",
            description=f"**{title}**",
            color=discord.Color.green()
        )

        # 建立控制按鈕
        view = MusicControlView(self, interaction.guild.id)

        # 發送 Embed
        await interaction.response.send_message(embed=embed, view=view)

        # 如果沒有正在播放，開始播放
        if not self.now_playing.get(interaction.guild.id):
            asyncio.create_task(self.start_playback(interaction.guild.id))

    async def start_playback(self, guild_id):
        q = self.queue[guild_id]
        vc = self.vc_dict[guild_id]
        while q:
            audio_url, title = q.pop(0)
            self.now_playing[guild_id] = title
            vc.play(FFmpegPCMAudio(audio_url, options="-vn"))
            # 等待播放完成
            while vc.is_playing():
                await asyncio.sleep(1)
            self.now_playing[guild_id] = None
            
class MusicControlView(discord.ui.View):
    def __init__(self, cog: VoiceCog, guild_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="⏯️ 暫停/播放", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.cog.vc_dict[self.guild_id]
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ 暫停播放", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ 繼續播放", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 目前沒有播放中的音樂", ephemeral=True)

    @discord.ui.button(label="⏭️ 跳過", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.cog.vc_dict[self.guild_id]
        if vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏩ 已跳過歌曲", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 目前沒有播放中的音樂", ephemeral=True)

    @discord.ui.button(label="⏹️ 停止", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.cog.vc_dict[self.guild_id]
        if vc.is_connected():
            vc.stop()
            await vc.disconnect()
            await interaction.response.send_message("⏹️ 已停止播放並離開語音頻道", ephemeral=True)
            self.cog.queue[self.guild_id] = []
            self.cog.now_playing[self.guild_id] = None
        else:
            await interaction.response.send_message("❌ 目前沒有連線的語音頻道", ephemeral=True)


# =========================
# ⚡ 錯誤處理和事件監聽
# =========================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """處理應用程式指令錯誤"""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"❌ 指令錯誤：{error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ 指令錯誤：{error}", ephemeral=True)
    except Exception:
        pass

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command):
    """處理成功執行的應用程式指令，並記錄日誌"""
    await log_command(interaction, f"/{command.qualified_name}")

@bot.event
async def on_ready():
    """機器人上線時執行"""
    print(f"✅ 機器人 {bot.user} 已上線！")
    try:
        await bot.tree.sync()
        print("✅ 指令已同步！")
    except Exception as e:
        print(f"❌ 指令同步失敗: {e}")
    
    # 在這裡註冊你的 Cog
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(ReactionRoleCog(bot))
    await bot.add_cog(FunCog(bot))
    await bot.add_cog(PingCog(bot))
    await bot.add_cog(HelpCog(bot))
    await bot.add_cog(VoiceCog(bot))
    

# =========================
# ⚡ Flask 路由
# =========================
@app.route("/")
def index():
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    if not user_data or not guilds_data:
        return render_template('login.html', auth_url=AUTH_URL)
    is_special_user = int(user_data['id']) in SPECIAL_USER_IDS
    ADMINISTRATOR_PERMISSION = 8 
    admin_guilds = [g for g in guilds_data if (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION]
    return render_template('dashboard.html', user=user_data, guilds=admin_guilds, is_special_user=is_special_user)

@app.route("/logs/all")
def all_guild_logs():
    user_data = session.get("discord_user")
    if not user_data or int(user_data['id']) not in SPECIAL_USER_IDS:
        return "❌ 您沒有權限訪問這個頁面。", 403
    return render_template('all_logs.html', logs=command_logs)

@app.route("/guild/<int:guild_id>")
async def guild_dashboard(guild_id):
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    if not user_data or not guilds_data:
        return redirect(url_for('index'))
    ADMINISTRATOR_PERMISSION = 8
    guild_found = any((int(g['id']) == guild_id and (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION) for g in guilds_data)
    if not guild_found:
        return "❌ 你沒有權限管理這個伺服器", 403
    try:
        guild_obj = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
        member_count = guild_obj.member_count
        is_owner = guild_obj.owner_id == int(user_data['id'])
    except (discord.NotFound, discord.Forbidden):
        return "❌ 找不到這個伺服器或沒有足夠權限", 404
    return render_template('guild_dashboard.html', user=user_data, guild_obj=guild_obj, member_count=member_count, is_owner=is_owner)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "授權失敗", 400
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": "identify guilds"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_response = requests.post(TOKEN_URL, data=data, headers=headers)
    token_response.raise_for_status()
    tokens = token_response.json()
    access_token = tokens["access_token"]
    user_headers = {"Authorization": f"Bearer {access_token}"}
    user_response = requests.get(USER_URL, headers=user_headers)
    user_response.raise_for_status()
    user_data = user_response.json()
    guilds_response = requests.get(f"{DISCORD_API_BASE_URL}/users/@me/guilds", headers=user_headers)
    guilds_response.raise_for_status()
    guilds_data = guilds_response.json()
    session["discord_user"] = user_data
    session["discord_guilds"] = guilds_data
    return redirect(url_for("index"))

@app.route("/logout")
def logout():
    session.pop("discord_user", None)
    session.pop("discord_guilds", None)
    return redirect(url_for("index"))


# =========================
# ⚡ 執行區塊
# =========================
def run_web():
    port = os.getenv("PORT", 8080)
    app.run(host="0.0.0.0", port=int(port), debug=False, use_reloader=False)

def keep_web_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

async def main():
    keep_web_alive()
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())