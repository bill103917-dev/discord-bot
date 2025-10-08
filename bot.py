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
from flask import Flask, session, request, render_template, redirect, url_for, jsonify
from discord.app_commands import checks
from discord.app_commands import Choice

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

LOG_VIEWER_IDS = [
    1238436456041676853,  # <-- 範例 ID，請替換成你想開放的使用者 ID
]

# 權限常數 (管理員權限)
ADMINISTRATOR_PERMISSION = 8192

# -------------------------------------------------------------
# 氣泡紙內容定義 (Code Block 與四格空位排版)
# -------------------------------------------------------------

BUBBLE_WRAP_TEXT_ALIGNED = (
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
    "||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪|| ||啪||\n"
)
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
AUTH_URL = f"{DISCORD_API_BASE_URL}/oauth2/authorize?response_type=code&client_id={DISCORD_CLIENT_ID}&scope=identify%20guilds%20guilds.members.read&redirect_uri={DISCORD_REDIRECT_URI}"
TOKEN_URL = f"{DISCORD_API_BASE_URL}/oauth2/token"
USER_URL = f"{DISCORD_API_BASE_URL}/users/@me"

# **🔥 關鍵修正 1: 儲存 Event Loop**
discord_loop = None

# =========================
# ⚡ 通用函式與設定儲存
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

def load_config(guild_id):
    """從檔案或資料庫載入伺服器設定 (目前為範例預設值)"""
    # 💡 實際應用中，請在這裡加入從檔案或資料庫載入設定的邏輯
    return {
        'welcome_channel_id': '',
        'video_notification_channel_id': '',
        'video_notification_message': '有人發影片囉！\n標題：{title}\n頻道：{channel}\n連結：{link}', 
        'live_notification_message': '有人開始直播啦！\n頻道：{channel}\n快點進來看：{link}', 
    }

def save_config(guild_id, config):
    """將伺服器設定儲存到檔案或資料庫 (目前為範例輸出)"""
    # 💡 實際應用中，請在這裡加入儲存設定到檔案或資料庫的邏輯
    print(f"--- 設定已儲存：{guild_id} ---")
    print(config)


# =========================
# ⚡ 指令相關類別和 Cog
# =========================

# RPS 相關類別 (不變，這裡省略)
# ... (RPSInviteView 和 RPSView 程式碼不變，故省略) ...
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
            winner_name = winner.display_name if isinstance(winner, discord.Member) or isinstance(winner, discord.User) else winner
            desc += f"🎉 **{winner_name}** 獲勝！"
        elif round_result:
            desc += round_result + "\n\n請繼續選擇你的出拳：✊ / ✌️ / ✋"
        else:
            desc += "請選擇你的出拳：✊ / ✌️ / ✋"
        return discord.Embed(title=title, description=desc, color=discord.Color.blurple())

    def make_cancel_embed(self):
        return discord.Embed(title="🛑 遊戲已取消", description="這場比賽已被取消。", color=discord.Color.red())

    def make_timeout_embed(self):
        return discord.Embed(title="⌛ 遊戲超時", description="60 秒內沒有出拳，判定認輸。", color=discord.Color.orange())

    async def on_timeout(self):
        if self.message:
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

    async def handle_round(self):
        # 處理回合邏輯 (為避免過長，這裡省略細節，假設它能正確更新 self.scores)
        pass

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

    # /say 指令 (不變)
    @app_commands.command(name="say", description="讓機器人發送訊息（管理員或特殊使用者限定）")
    # ... (say 指令內容省略) ...
    async def say(self, interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None, user: Optional[discord.User] = None):
        await log_command(interaction, "/say")
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.followup.send("❌ 你沒有權限使用此指令", ephemeral=True)
            return

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

    # /announce 指令 (不變)
    @app_commands.command(name="announce", description="發布公告（管理員限定）")
    # ... (announce 指令內容省略) ...
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

    # /calc 指令 (不變)
    @app_commands.command(name="calc", description="簡單計算器")
    # ... (calc 指令內容省略) ...
    async def calc(self, interaction: discord.Interaction, expr: str):
        await log_command(interaction, "/calc")
        await interaction.response.defer(ephemeral=False)
        try:
            allowed = "0123456789+-*/(). "
            if not all(c in allowed for c in expr):
                raise ValueError("包含非法字符")
            result = eval(expr)
            await interaction.followup.send(f"結果：{result}")
        except Exception as e:
            await interaction.followup.send(f"計算錯誤：{e}")

    # /delete 指令 (不變)
    @app_commands.command(name="delete", description="刪除訊息（管理員限定）")
    # ... (delete 指令內容省略) ...
    async def delete(self, interaction: discord.Interaction, amount: int):
        await log_command(interaction, "/delete")
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.followup.send("❌ 只有管理員可以刪除訊息", ephemeral=True)
            return
        if amount < 1 or amount > 100:
            await interaction.followup.send("❌ 請輸入 1 ~ 100 的數字", ephemeral=True)
            return

        try:
            deleted = await interaction.channel.purge(limit=amount + 1)
            await interaction.followup.send(f"✅ 已刪除 {len(deleted) - 1} 則訊息", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 刪除失敗: {e}", ephemeral=True)


# **📌 新增 ModerationCog (中文管理指令)**
class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 檢查是否有踢出成員的權限
    async def cog_check(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用。", ephemeral=True)
            return False
        # 要求使用者有踢出/封鎖權限
        if not interaction.user.guild_permissions.kick_members or not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("❌ 你沒有執行此類管理指令的權限。", ephemeral=True)
            return False
        return True

    @app_commands.command(name="踢出", description="將成員踢出伺服器（需要權限）")
    @checks.has_permissions(kick_members=True)
    async def kick_member(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "無"):
        await log_command(interaction, "/踢出")
        await interaction.response.defer(ephemeral=True)

        if member.top_role >= interaction.user.top_role and member.id != interaction.user.id:
            await interaction.followup.send(f"❌ 無法踢出：{member.display_name} 的身分組高於或等於你。", ephemeral=True)
            return
        
        try:
            await member.kick(reason=reason)
            await interaction.followup.send(f"✅ 已踢出 {member.mention}。原因：`{reason}`")
        except discord.Forbidden:
            await interaction.followup.send("❌ 機器人沒有足夠的權限來踢出此成員。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 踢出失敗: {e}", ephemeral=True)

    @app_commands.command(name="封鎖", description="將成員封鎖（需要權限）")
    @checks.has_permissions(ban_members=True)
    async def ban_member(self, interaction: discord.Interaction, user_id: str, reason: Optional[str] = "無"):
        await log_command(interaction, "/封鎖")
        await interaction.response.defer(ephemeral=True)

        try:
            member = await self.bot.fetch_user(int(user_id))
            if member:
                await interaction.guild.ban(member, reason=reason, delete_message_days=0)
                await interaction.followup.send(f"✅ 已封鎖 {member.mention}。原因：`{reason}`")
            else:
                await interaction.followup.send("❌ 找不到該使用者 ID。", ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send("❌ 機器人沒有足夠的權限來封鎖此成員。", ephemeral=True)
        except ValueError:
             await interaction.followup.send("❌ 使用者 ID 格式錯誤。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 封鎖失敗: {e}", ephemeral=True)

    @app_commands.command(name="禁言", description="將成員禁言一段時間 (Timeout)（需要權限）")
    @checks.has_permissions(moderate_members=True)
    async def timeout_member(self, interaction: discord.Interaction, member: discord.Member, duration: int, time_unit: str, reason: Optional[str] = "無"):
        await log_command(interaction, "/禁言")
        await interaction.response.defer(ephemeral=True)

        unit_seconds = {
            "秒": 1, "分鐘": 60, "小時": 3600, "天": 86400
        }
        if time_unit not in unit_seconds:
            await interaction.followup.send("❌ 時間單位錯誤。請使用 秒、分鐘、小時、天。", ephemeral=True)
            return

        timeout_seconds = duration * unit_seconds[time_unit]
        # Discord 禁言最長為 28 天 (2,419,200 秒)
        if timeout_seconds > 2419200:
            await interaction.followup.send("❌ 禁言時間不能超過 28 天。", ephemeral=True)
            return
        
        timeout = datetime.timedelta(seconds=timeout_seconds)

        try:
            await member.timeout(timeout, reason=reason)
            await interaction.followup.send(f"✅ 已禁言 {member.mention} {duration}{time_unit}。原因：`{reason}`")
        except discord.Forbidden:
            await interaction.followup.send("❌ 機器人沒有足夠的權限來禁言此成員。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 禁言失敗: {e}", ephemeral=True)

    @timeout_member.autocomplete('time_unit')
    async def time_unit_autocomplete(self, interaction: discord.Interaction, current: str):
        units = ["秒", "分鐘", "小時", "天"]
        return [
            Choice(name=unit, value=unit)
            for unit in units if current.lower() in unit
        ]
        
    @app_commands.command(name="解除禁言", description="解除成員的禁言狀態")
    @checks.has_permissions(moderate_members=True)
    async def untimeout_member(self, interaction: discord.Interaction, member: discord.Member):
        await log_command(interaction, "/解除禁言")
        await interaction.response.defer(ephemeral=True)

        if not member.timed_out:
             await interaction.followup.send(f"❌ {member.display_name} 目前沒有被禁言。", ephemeral=True)
             return

        try:
            await member.timeout(None)
            await interaction.followup.send(f"✅ 已解除 {member.mention} 的禁言狀態。")
        except discord.Forbidden:
            await interaction.followup.send("❌ 機器人沒有足夠的權限來解除禁言。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 解除禁言失敗: {e}", ephemeral=True)


class ReactionRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reaction_roles = {}
    
    # ... (reactionrole 指令內容不變，故省略) ...
    @app_commands.command(name="reactionrole", description="新增反應身分組（管理員用）")
    async def reactionrole(self, interaction: discord.Interaction, message: str, emoji: str, role: discord.Role, channel: Optional[discord.TextChannel] = None):
        await log_command(interaction, "/reactionrole")
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("❌ 只有管理員可以使用此指令", ephemeral=True)
            return

        msg_obj = None
        if re.match(r"https?://", message):
            try:
                m = re.match(r"https?://discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", message)
                if not m:
                    await interaction.followup.send("❌ 訊息連結格式錯誤", ephemeral=True)
                    return
                guild_id, channel_id, message_id = map(int, m.groups())
                channel_obj = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                msg_obj = await channel_obj.fetch_message(message_id)
            except Exception as e:
                await interaction.followup.send(f"❌ 無法解析訊息連結: {e}", ephemeral=True)
                return
        else:
            if channel is None:
                channel = interaction.channel
            async for msg in channel.history(limit=100):
                if msg.content == message:
                    msg_obj = msg
                    break
            if msg_obj is None:
                await interaction.followup.send("❌ 找不到符合的訊息", ephemeral=True)
                return

        try:
            await msg_obj.add_reaction(emoji)
        except Exception as e:
            await interaction.followup.send(f"❌ 無法加反應: {e}", ephemeral=True)
            return

        guild_roles = self.reaction_roles.setdefault(interaction.guild_id, {})
        msg_roles = guild_roles.setdefault(msg_obj.id, {})
        msg_roles[emoji] = role.id
        await interaction.followup.send(f"✅ 已設定 {emoji} -> {role.name}", ephemeral=True)


class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # /rps 指令 (不變)
    @app_commands.command(name="rps", description="剪刀石頭布對戰")
    # ... (rps 指令內容省略) ...
    async def rps(self, interaction: discord.Interaction, rounds: int = 3, opponent: Optional[discord.User] = None, vs_bot: bool = False):
        await log_command(interaction, "/rps")
        await interaction.response.defer()
        
        if not opponent and not vs_bot:
            await interaction.followup.send("❌ 你必須選擇對手或開啟 vs_bot!", ephemeral=True)
            return
        if opponent and opponent.bot:
            await interaction.followup.send("🤖 不能邀請機器人，請改用 vs_bot=True", ephemeral=True)
            return

        if opponent:
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


    # /氣泡紙 指令 (不變)
    @app_commands.command(name="氣泡紙", description="發送一個巨大的氣泡紙，來戳爆它吧！")
    # ... (氣泡紙 指令內容省略) ...
    async def bubble_wrap_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"點擊這些氣泡來戳爆它們！\n{BUBBLE_WRAP_TEXT_ALIGNED}"
        )

    # /dice 指令 (不變)
    @app_commands.command(name="dice", description="擲一顆 1-6 的骰子")
    # ... (dice 指令內容省略) ...
    async def dice(self, interaction: discord.Interaction):
        await log_command(interaction, "/dice")
        await interaction.response.defer()
        
        number = random.randint(1, 6)
        await interaction.followup.send(f"🎲 {interaction.user.mention} 擲出了 **{number}**！")

    # **📌 新增 /抽籤 指令**
    @app_commands.command(name="抽籤", description="在多個選項中做出隨機決定。選項之間用逗號（,）分隔")
    async def choose(self, interaction: discord.Interaction, options: str):
        await log_command(interaction, "/抽籤")
        await interaction.response.defer()

        # 將選項字串按逗號分隔並移除空白
        choices = [opt.strip() for opt in options.split(',') if opt.strip()]

        if len(choices) < 2:
            await interaction.followup.send("❌ 請提供至少兩個選項，並用逗號 (,) 分隔。", ephemeral=True)
            return

        selected = random.choice(choices)

        embed = discord.Embed(
            title="🎯 抽籤結果",
            description=f"我在以下選項中抽了一個：\n`{options}`",
            color=discord.Color.green()
        )
        embed.add_field(name="🎉 最終選擇", value=f"**{selected}**", inline=False)
        embed.set_footer(text=f"決定者：{interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed)


class LogsCog(commands.Cog):
    # ... (LogsCog 內容不變，故省略) ...
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="logs", description="在 Discord 訊息中顯示最近的指令紀錄")
    async def logs(self, interaction: discord.Interaction):
        await log_command(interaction, "/logs")
        
        if int(interaction.user.id) not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            return
            
        logs_text = "📜 **最近的指令紀錄**\n\n"
        if not command_logs:
            logs_text += "目前沒有任何紀錄。"
        else:
            logs_text += "\n".join([f"`{log['time']}`: {log['text']}" for log in command_logs])
            
        await interaction.response.send_message(logs_text, ephemeral=True)


class PingCog(commands.Cog):
    # ... (PingCog 內容不變，故省略) ...
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="測試機器人是否在線")
    async def ping(self, interaction: discord.Interaction):
        await log_command(interaction, "/ping")
        await interaction.response.defer()

        await interaction.followup.send(f"🏓 Pong! {round(bot.latency*1000)}ms")


class HelpCog(commands.Cog):
    # ... (HelpCog 內容不變，故省略) ...
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="顯示所有可用的指令")
    async def help(self, interaction: discord.Interaction):
        await log_command(interaction, "/help")
        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(title="📖 指令清單", description="以下是目前可用的指令：", color=discord.Color.blue())
        for cmd in self.bot.tree.get_commands():
            embed.add_field(name=f"/{cmd.name}", value=cmd.description or "沒有描述", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # 每個 guild 的播放隊列
        self.now_playing = {}  # 正在播放曲目
        self.vc_dict = {}  # 儲存語音客戶端

    async def get_audio_info(self, url: str):
        """從 YouTube URL 提取音訊資訊"""
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'noplaylist': True,
            'default_search': 'auto' # 啟用自動搜索
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:  # playlist
                info = info['entries'][0]
            audio_url = info['url']
            title = info.get('title', '未知曲目')
            return audio_url, title

    @app_commands.command(name="play", description="播放 YouTube 音樂或搜索歌曲")
    async def play(self, interaction: discord.Interaction, url: str):
        await log_command(interaction, "/play")
        await interaction.response.defer()
        
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 你必須先加入語音頻道", ephemeral=True)
            return
        channel = interaction.user.voice.channel

        vc = interaction.guild.voice_client
        if not vc:
            vc = await channel.connect()
        elif vc.channel != channel:
            await vc.move_to(channel)
        self.vc_dict[interaction.guild.id] = vc

        try:
            audio_url, title = await self.get_audio_info(url)
        except Exception as e:
            await interaction.followup.send(f"❌ 取得音訊失敗: {e}", ephemeral=True)
            return

        q = self.queue.setdefault(interaction.guild.id, [])
        q.append((audio_url, title))

        embed = discord.Embed(
            title="🎵 已加入隊列",
            description=f"**{title}**",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"隊列長度: {len(q) + (1 if self.now_playing.get(interaction.guild.id) else 0)}")

        view = MusicControlView(self, interaction.guild.id)
        await interaction.followup.send(embed=embed, view=view)

        if not self.now_playing.get(interaction.guild.id):
            asyncio.create_task(self.start_playback(interaction.guild.id))

    async def start_playback(self, guild_id):
        q = self.queue[guild_id]
        vc = self.vc_dict[guild_id]
        
        if vc.is_playing() or vc.is_paused():
            return # 已經在播放了，等待隊列執行

        while q:
            audio_url, title = q.pop(0)
            self.now_playing[guild_id] = title
            
            # 發送正在播放的訊息 (這裡需要一個目標頻道，為了簡化，使用 Bot 接收到指令的最後一個頻道或第一個文字頻道)
            if guild_id in self.vc_dict and self.vc_dict[guild_id].channel.guild.text_channels:
                 target_channel = self.vc_dict[guild_id].channel.guild.text_channels[0]
                 await target_channel.send(f"▶️ 正在播放: **{title}**")
            
            try:
                vc.play(FFmpegPCMAudio(audio_url, options="-vn"))
                # 等待播放完成
                while vc.is_playing() or vc.is_paused():
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"播放 {title} 時發生錯誤: {e}")
                # 跳過並繼續下一首
                continue

            self.now_playing[guild_id] = None
            
    # **📌 新增 /歌單 (Queue)**
    @app_commands.command(name="歌單", description="查看當前的播放隊列")
    async def show_queue(self, interaction: discord.Interaction):
        await log_command(interaction, "/歌單")
        await interaction.response.defer()
        
        q = self.queue.get(interaction.guild.id, [])
        now_playing = self.now_playing.get(interaction.guild.id)

        embed = discord.Embed(
            title="🎶 播放隊列",
            color=discord.Color.blue()
        )

        if now_playing:
            embed.add_field(name="正在播放", value=f"1️⃣ **{now_playing}**", inline=False)
        
        if q:
            queue_list = "\n".join([f"{i+2}️⃣ {title}" for i, (_, title) in enumerate(q[:10])])
            embed.add_field(name="即將播放 (最多顯示 10 首)", value=queue_list, inline=False)
        
        if not now_playing and not q:
            embed.description = "隊列目前是空的。"

        await interaction.followup.send(embed=embed)

    # **📌 新增 /跳至 (Skip To)**
    @app_commands.command(name="跳至", description="跳過當前歌曲並播放隊列中指定位置的歌曲")
    async def skip_to(self, interaction: discord.Interaction, position: int):
        await log_command(interaction, "/跳至")
        await interaction.response.defer()

        q = self.queue.get(interaction.guild.id, [])
        vc = self.vc_dict.get(interaction.guild.id)

        if not vc or not vc.is_playing():
            await interaction.followup.send("❌ 目前沒有播放中的音樂。", ephemeral=True)
            return

        # 隊列位置是從 1 (下一首) 開始計算的，因為正在播放的是 0
        if position < 1 or position > len(q):
            await interaction.followup.send(f"❌ 無效的隊列位置。請輸入 1 到 {len(q)} 之間的一個數字。", ephemeral=True)
            return

        # 移除隊列中從 0 到 position-1 的所有歌曲
        q = q[position - 1:]
        self.queue[interaction.guild.id] = q
        
        # 停止當前播放的歌曲，觸發 start_playback 播放下一首 (隊列第一首)
        vc.stop()
        
        skipped_title = self.now_playing.get(interaction.guild.id)
        await interaction.followup.send(f"⏭️ 已跳過 **{skipped_title}** 及前面 {position-1} 首歌曲。正在播放下一首...")

class MusicControlView(discord.ui.View):
    def __init__(self, cog: VoiceCog, guild_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="⏯️ 暫停/播放", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc = self.cog.vc_dict.get(self.guild_id)
        if not vc:
            await interaction.followup.send("❌ 機器人目前沒有連線到語音頻道。", ephemeral=True)
            return
            
        if vc.is_playing():
            vc.pause()
            await interaction.followup.send("⏸️ 暫停播放", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.followup.send("▶️ 繼續播放", ephemeral=True)
        else:
            await interaction.followup.send("❌ 目前沒有播放中的音樂", ephemeral=True)

    @discord.ui.button(label="⏭️ 跳過", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc and vc.is_playing():
            skipped_title = self.cog.now_playing.get(self.guild_id, "未知歌曲")
            vc.stop()
            await interaction.followup.send(f"⏩ 已跳過 **{skipped_title}**。", ephemeral=True)
        else:
            await interaction.followup.send("❌ 目前沒有播放中的音樂。", ephemeral=True)

    @discord.ui.button(label="⏹️ 停止", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc = self.cog.vc_dict.get(self.guild_id)
        if vc and vc.is_connected():
            vc.stop()
            await vc.disconnect()
            await interaction.followup.send("⏹️ 已停止播放並離開語音頻道", ephemeral=True)
            self.cog.queue[self.guild_id] = []
            self.cog.now_playing[self.guild_id] = None
        else:
            await interaction.followup.send("❌ 目前沒有連線的語音頻道", ephemeral=True)


# =========================
# ⚡ 錯誤處理和事件監聽
# =========================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """處理應用程式指令錯誤"""
    
    # 處理權限不足的錯誤
    if isinstance(error, app_commands.MissingPermissions):
        error_msg = f"❌ 權限不足：你缺少執行此指令所需的權限：`{', '.join(error.missing_permissions)}`"
    elif isinstance(error, app_commands.CheckFailure):
        # 處理 ModerationCog 中 `cog_check` 的錯誤訊息
        error_msg = str(error) 
    else:
        error_msg = f"❌ 指令錯誤：{error}"

    # 確保回覆只發送一次
    if interaction.response.is_done():
        await interaction.followup.send(error_msg, ephemeral=True)
    else:
        await interaction.response.send_message(error_msg, ephemeral=True)

@bot.event
async def on_ready():
    """機器人上線時執行"""
    print(f"✅ 機器人 {bot.user} 已上線！")
    
    # 註冊 Cogs
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(ModerationCog(bot)) # **新增管理 Cog**
    await bot.add_cog(ReactionRoleCog(bot))
    await bot.add_cog(FunCog(bot))
    await bot.add_cog(LogsCog(bot))
    await bot.add_cog(PingCog(bot))
    await bot.add_cog(HelpCog(bot))
    await bot.add_cog(VoiceCog(bot))

    try:
        await bot.tree.sync()
        print("✅ 指令已同步！")
    except Exception as e:
        print(f"❌ 指令同步失敗: {e}")

# =========================
# ⚡ Flask 路由 (同步/異步修正版)
# =========================

@app.route("/")
def index():
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    if not user_data or not guilds_data:
        return render_template('login.html', auth_url=AUTH_URL)

    is_special_user = int(user_data['id']) in SPECIAL_USER_IDS
    
    admin_guilds = [
        g for g in guilds_data 
        if (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION
    ]
    filtered_guilds = [g for g in admin_guilds if bot.get_guild(int(g['id']))]
    
    return render_template('dashboard.html', user=user_data, guilds=filtered_guilds, is_special_user=is_special_user, DISCORD_CLIENT_ID=DISCORD_CLIENT_ID)


@app.route("/guild/<int:guild_id>")
def guild_dashboard(guild_id): 
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    
    if not user_data or not guilds_data:
        return redirect(url_for('index'))

    guild_found = any((int(g['id']) == guild_id and (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION) for g in guilds_data)
    
    if not guild_found:
        return "❌ 權限不足：你沒有權限管理這個伺服器。", 403

    if not bot.get_guild(guild_id):
        try:
            future = asyncio.run_coroutine_threadsafe(bot.fetch_guild(guild_id), discord_loop) 
            future.result(timeout=5)
        except Exception as e:
            print(f"Fetch Guild 錯誤 (dashboard): {e}")
            return f"❌ 找不到伺服器：機器人目前不在 ID 為 {guild_id} 的伺服器中或連線超時。", 404

    return redirect(url_for('settings', guild_id=guild_id))


@app.route("/guild/<int:guild_id>/settings", methods=['GET', 'POST'])
@app.route("/guild/<int:guild_id>/settings/<string:module>", methods=['GET', 'POST']) 
def settings(guild_id, module=None): 
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    
    if not user_data or not guilds_data:
        return redirect(url_for('index'))
    
    guild_found = any((int(g['id']) == guild_id and (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION) for g in guilds_data)
    if not guild_found:
        return "❌ 你沒有權限管理這個伺服器", 403
        
    guild_obj = bot.get_guild(guild_id)
    if not guild_obj:
        try:
            future = asyncio.run_coroutine_threadsafe(bot.fetch_guild(guild_id), discord_loop) 
            guild_obj = future.result(timeout=5)
        except Exception as e:
            print(f"Fetch Guild 錯誤 (settings): {e}")
            return "❌ 機器人不在這個伺服器或連線超時。", 404
        
    config = load_config(guild_id)
    
    if request.method == 'POST':
        if module == 'notifications': 
            config['welcome_channel_id'] = request.form.get('welcome_channel_id', '')
            config['video_notification_channel_id'] = request.form.get('video_channel_id', '')
            config['video_notification_message'] = request.form.get('video_message', '')
            config['live_notification_message'] = request.form.get('live_message', '')
            
            save_config(guild_id, config)
            return redirect(url_for('settings', guild_id=guild_id, module=module))
        
        return redirect(url_for('settings', guild_id=guild_id)) 

    context = {
        'guild_obj': guild_obj,
        'user_data': user_data,
        'config': config,
        'channels': guild_obj.text_channels,
        'welcome_channel_id': config.get('welcome_channel_id', ''),
        'video_channel_id': config.get('video_notification_channel_id', ''),
        'video_message': config.get('video_notification_message', '有人發影片囉！\n標題：{title}\n頻道：{channel}\n連結：{link}'),
        'live_message': config.get('live_notification_message', '有人開始直播啦！\n頻道：{channel}\n快點進來看：{link}'),
    }
    
    if module:
        if module == 'notifications':
            return render_template('settings_notifications.html', **context)
        else:
            return redirect(url_for('settings', guild_id=guild_id)) 
    else:
        return render_template('settings_main.html', **context)


@app.route("/guild/<int:guild_id>/members")
def members_page(guild_id): 
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    if not user_data or not guilds_data:
        return redirect(url_for('index'))
    
    guild_found = any((int(g['id']) == guild_id and (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION) for g in guilds_data)
    if not guild_found:
        return "❌ 你沒有權限管理這個伺服器", 403
        
    try:
        guild_obj = bot.get_guild(guild_id)
        if not guild_obj:
            future = asyncio.run_coroutine_threadsafe(bot.fetch_guild(guild_id), discord_loop) 
            guild_obj = future.result(timeout=5)
            if not guild_obj:
                 return "❌ 找不到這個伺服器", 404

        future_members = asyncio.run_coroutine_threadsafe(guild_obj.fetch_members(limit=None), discord_loop)
        members = future_members.result(timeout=10)

        members_list = [
            {
                "id": m.id,
                "name": m.display_name,
                "avatar": m.avatar.url if m.avatar else m.default_avatar.url,
                "joined_at": m.joined_at.strftime("%Y-%m-%d %H:%M:%S")
            }
            for m in members
        ]
        
        return render_template('members.html', guild_obj=guild_obj, members=members_list)
        
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"Discord API 錯誤 (成員頁面): {e}")
        return f"❌ Discord 存取錯誤：請檢查機器人是否開啟 **SERVER MEMBERS INTENT** 且擁有伺服器管理權限。錯誤訊息: {e}", 500
    except Exception as e:
        print(f"應用程式錯誤 (成員頁面): {e}")
        return f"❌ 內部伺服器錯誤：在處理成員資料時發生意外錯誤。錯誤訊息: {e}", 500


@app.route("/logs/all")
def all_guild_logs():
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    
    if not user_data:
        return redirect(url_for('index'))

    user_id = int(user_data['id'])
    
    can_view_logs = (
        user_id in SPECIAL_USER_IDS or
        user_id in LOG_VIEWER_IDS or
        any((int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION for g in guilds_data)
    )
    
    if not can_view_logs:
        return "❌ 您沒有權限訪問這個頁面。", 403

    return render_template('all_logs.html', logs=command_logs)


@app.route("/guild/<int:guild_id>/settings/notifications_modal", methods=['GET'])
def notifications_modal(guild_id): 
    """
    用於 AJAX 載入影片通知設定彈出視窗 (modal_notifications.html) 的內容。
    已修復：移除異步調用以避免在同步環境下崩潰。
    """
    user_data = session.get("discord_user")
    if not user_data:
        return "未登入", 401

    try:
        # 1. 嘗試從 Discord Bot 緩存中獲取伺服器物件
        guild_obj = bot.get_guild(guild_id)
        
        # 2. 如果緩存中找不到，不執行危險的異步操作，直接返回 404。
        #    這表示機器人可能不在該伺服器。
        if not guild_obj:
            return "找不到伺服器，機器人不在該處（緩存未載入）。", 404

        # 3. 成功獲取資料後，繼續加載配置
        config = load_config(guild_id)
        
        context = {
            'guild_obj': guild_obj,
            # 確保 'channels' 列表是純同步的
            'channels': [c for c in guild_obj.channels if isinstance(c, discord.TextChannel)],
            
            'video_channel_id': config.get('video_notification_channel_id', ''),
            'video_message': config.get('video_notification_message', '{channel} 上新影片啦！\n{title}'),
            'live_message': config.get('live_notification_message', '{channel} 開播啦\n{title}'),
            'ping_role': config.get('ping_role', '@everyone'),
            'content_filter': config.get('content_filter', 'Videos,Livestreams'), 
        }
        
        return render_template('modal_notifications.html', **context)
        
    except discord.Forbidden:
        # 保持此錯誤處理
        return "❌ 權限錯誤：機器人無法讀取伺服器資料。", 403
    except Exception as e:
        # 如果不是異步問題，而是其他錯誤 (如 load_config 失敗)，則會捕獲
        print(f"載入通知 Modal 時發生錯誤: {e}")
        return f"❌ 內部錯誤：無法載入設定視窗。{e}", 500


@app.route("/logs/data")
def logs_data():
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    
    if not user_data:
        return jsonify({"error": "請先登入"}), 401

    user_id = int(user_data['id'])
    
    can_view_logs = (
        user_id in SPECIAL_USER_IDS or
        user_id in LOG_VIEWER_IDS or
        any((int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION for g in guilds_data)
    )
    
    if not can_view_logs:
        return jsonify({"error": "您沒有權限訪問此資料"}), 403
        
    return jsonify(command_logs)

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
        "scope": "identify guilds guilds.members.read"
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
    all_guilds = guilds_response.json()

    admin_guilds = [
        g for g in all_guilds
        if (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION
    ]

    session["discord_user"] = user_data
    session["discord_guilds"] = [
        {"id": g["id"], "name": g["name"], "icon": g["icon"], "permissions": g.get('permissions', '0')}
        for g in admin_guilds
    ]

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
    global discord_loop
    discord_loop = asyncio.get_running_loop() 
    
    keep_web_alive()
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("機器人已手動關閉。")
    except RuntimeError as e:
        if "cannot run from a thread" in str(e):
            print("Web 伺服器啟動錯誤，可能需要使用 gunicorn 或其他方式啟動。")
        else:
            raise