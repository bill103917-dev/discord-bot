# bot.py — 單一檔案完整版本
# 注意：執行前請確定已安裝依賴：discord.py, flask, psycopg2-binary (若使用 DB), yt-dlp, pytube (若需要)
# pip install -U "discord.py" flask yt-dlp pytube psycopg2-binary

import os
import sys
import re
import json
import time
import random
import threading
import asyncio
import traceback
from typing import Optional, List, Dict, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands, ui, Interaction, TextChannel
from flask import Flask, session, request, render_template, redirect, url_for, jsonify

# Optional imports
try:
    import yt_dlp
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except Exception:
    yt_dlp = None
    YoutubeDL = None
    DownloadError = Exception

try:
    import psycopg2
except Exception:
    psycopg2 = None

# =========================
# Basic config from env
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", os.urandom(24))
PORT = int(os.getenv("PORT", 8080))

if not TOKEN:
    print("❌ DISCORD_TOKEN not set. Exiting.")
    sys.exit(1)

# =========================
# Utils & Shared State
# =========================
def safe_now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

def to_thread(func):
    import functools
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)
    return wrapper

async def log_command(interaction, command_name: str):
    try:
        guild_name = interaction.guild.name if interaction.guild else "DM"
        guild_id = interaction.guild.id if interaction.guild else None
        entry = {"time": safe_now(), "text": f"{interaction.user} 在 {guild_name}({guild_id}) 執行 {command_name}"}
        COMMAND_LOGS.append(entry)
        # keep max 200 logs
        if len(COMMAND_LOGS) > 200:
            COMMAND_LOGS.pop(0)
        print(f"[LOG] {entry['time']} - {entry['text']}")
    except Exception:
        print(f"[LOG] {safe_now()} - {command_name} executed (no interaction details).")

# Shared globals
COMMAND_LOGS: List[Dict] = []
SPECIAL_USER_IDS = [1238436456041676853]   # 請替換
LOG_VIEWER_IDS = [1238436456041676853]
HUNDRED_PERCENT_IDS = [1343900739407319070]
ADMINISTRATOR_PERMISSION = 0x00000008  # administrator bit

# =========================
# Bot + Intents
# =========================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# We'll keep a reference to asyncio loop for Flask -> Discord threadsafe calls
discord_loop = None

# =========================
# Helper: safe send DM
# =========================
async def safe_send_user(user: discord.User, embed: Optional[discord.Embed] = None, content: Optional[str] = None):
    try:
        if embed and content:
            await user.send(content=content, embed=embed)
        elif embed:
            await user.send(embed=embed)
        else:
            await user.send(content)
        return True
    except discord.Forbidden:
        return False
    except Exception as e:
        print("safe_send_user error:", e)
        return False

# =========================
# -- Views & Modal (Support)
# =========================

class ReplyModal(ui.Modal, title='回覆用戶問題'):
    response_title = ui.TextInput(label='回覆標題 (可選)', required=False, max_length=100)
    response_content = ui.TextInput(label='回覆內容', style=discord.TextStyle.long, required=True, max_length=1500)

    def __init__(self, original_user_id: int, original_content: str, cog):
        super().__init__()
        self.original_user_id = original_user_id
        self.original_content = original_content
        self.cog = cog
        self.admin_message = None

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        user_obj = self.cog.bot.get_user(self.original_user_id)
        admin_name = interaction.user.display_name
        reply_content = str(self.response_content)

        embed = discord.Embed(
            title=str(self.response_title).strip() or "管理員回覆",
            description=f"<@{interaction.user.id}> 說：\n>>> {reply_content}",
            color=discord.Color.green()
        )
        embed.add_field(name="您的原問題", value=f"```\n{self.original_content[:1000]}{'...' if len(self.original_content) > 1000 else ''}\n```", inline=False)

        if user_obj:
            try:
                await user_obj.send(embed=embed)
                await interaction.followup.send("✅ 回覆已成功發送。", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ 無法私訊用戶（被封鎖或關閉私訊）。", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 發送失敗: {e}", ephemeral=True)
        else:
            await interaction.followup.send("❌ 找不到該用戶。", ephemeral=True)


class ReplyView(ui.View):
    def __init__(self, original_user_id: int, original_content: str, cog):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id
        self.original_content = original_content
        self.cog = cog

    @ui.button(label='回覆問題', style=discord.ButtonStyle.success, emoji="💬")
    async def reply_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ 您沒有權限回覆此問題。", ephemeral=True)
            return
        modal = ReplyModal(self.original_user_id, self.original_content, self.cog)
        await interaction.response.send_modal(modal)

    @ui.button(label='停止回覆/已處理', style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ 您沒有權限操作此按鈕。", ephemeral=True)
            return
        msg = interaction.message
        embed = msg.embeds[0] if msg.embeds else discord.Embed(title="已處理")
        embed.title = f"🛑 已處理 - 由 {interaction.user.display_name}"
        finished_view = ui.View(timeout=None)
        finished_view.add_item(ui.Button(label=f'已由 {interaction.user.display_name} 標記為處理完畢', style=discord.ButtonStyle.secondary, disabled=True))
        await interaction.response.edit_message(embed=embed, view=finished_view)
        await interaction.followup.send("✅ 已標記為處理完畢。", ephemeral=True)

# =========================
# RPS Game Views
# =========================

class RPSInviteView(ui.View):
    def __init__(self, challenger: discord.User, opponent: discord.User, rounds: int):
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

    @ui.button(label="✅ 接受", style=discord.ButtonStyle.success)
    async def accept(self, interaction: Interaction, button: ui.Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("❌ 只有被邀請的人可以按！", ephemeral=True)
            return
        self.value = True
        await interaction.response.edit_message(content=f"{self.opponent.mention} 接受了挑戰！", embed=None, view=None)
        self.stop()

    @ui.button(label="❌ 拒絕", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: Interaction, button: ui.Button):
        if interaction.user != self.opponent:
            await interaction.response.send_message("❌ 只有被邀請的人可以按！", ephemeral=True)
            return
        self.value = False
        await interaction.response.edit_message(content=f"{self.opponent.mention} 拒絕了挑戰。", embed=None, view=None)
        self.stop()

class RPSView(ui.View):
    def __init__(self, player1: discord.User, player2: Optional[discord.User] = None, rounds: int = 3, vs_bot: bool = False):
        super().__init__(timeout=60)
        self.player1 = player1
        self.player2 = player2
        self.vs_bot = vs_bot
        self.rounds = rounds
        self.current_round = 1
        self.scores = {player1: 0}
        if player2:
            self.scores[player2] = 0
        else:
            self.scores["bot"] = 0
        self.choices = {}
        self.message = None

    def make_embed(self, game_over=False, winner=None, round_result=None):
        title = f"🎮 剪刀石頭布 - 第 {self.current_round} 回合 / 搶 {self.rounds} 勝"
        p1_score = self.scores.get(self.player1, 0)
        p2_score = self.scores.get(self.player2, 0) if self.player2 else self.scores.get("bot", 0)
        opponent_name = self.player2.mention if self.player2 else "🤖 機器人"
        desc = f"🏆 **比分**：{self.player1.mention} **{p1_score}** - **{p2_score}** {opponent_name}\n\n"
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

    @ui.button(label="✊", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: Interaction, button: ui.Button):
        await self.make_choice(interaction, "✊")

    @ui.button(label="✌️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: Interaction, button: ui.Button):
        await self.make_choice(interaction, "✌️")

    @ui.button(label="✋", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: Interaction, button: ui.Button):
        await self.make_choice(interaction, "✋")

    @ui.button(label="❌ 取消遊戲", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: Interaction, button: ui.Button):
        if interaction.user != self.player1 and interaction.user != self.player2:
            await interaction.response.send_message("❌ 只有參加玩家可以取消遊戲！", ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.make_cancel_embed(), view=None, content=None)
        active_games.pop(self.player1.id, None)
        self.stop()

    async def determine_winner(self, p1_choice, p2_choice):
        rules = {"✊": "✌️", "✌️": "✋", "✋": "✊"}
        if p1_choice == p2_choice:
            return "平手"
        elif rules[p1_choice] == p2_choice:
            return "P1"
        else:
            return "P2"

    async def handle_round(self):
        p1_choice = self.choices.get(self.player1)
        if p1_choice is None:
            return
        if self.vs_bot:
            p2_name = "🤖 機器人"
            p2_choice = random.choice(["✊", "✌️", "✋"])
            self.choices["bot"] = p2_choice
            round_winner = await self.determine_winner(p1_choice, p2_choice)
        else:
            p2_name = self.player2.display_name
            p2_choice = self.choices.get(self.player2)
            round_winner = await self.determine_winner(p1_choice, p2_choice)

        result_text = f"{self.player1.display_name} 出 **{p1_choice}** vs {p2_name} 出 **{p2_choice}**\n"
        winner_name = None
        if round_winner == "P1":
            self.scores[self.player1] += 1
            winner_name = self.player1.display_name
            result_text += f"🎉 {winner_name} 贏了這一回合！"
        elif round_winner == "P2":
            p2_obj = self.player2 if self.player2 else "bot"
            self.scores[p2_obj] += 1
            winner_name = self.player2.display_name if self.player2 else "🤖 機器人"
            result_text += f"🎉 {winner_name} 贏了這一回合！"
        else:
            result_text += "🤝 平手！"

        p1_score = self.scores.get(self.player1, 0)
        p2_score = self.scores.get(self.player2, 0) if self.player2 else self.scores.get("bot", 0)

        if p1_score >= self.rounds or p2_score >= self.rounds:
            final_winner = self.player1 if p1_score > p2_score else (self.player2 if self.player2 else "🤖 機器人")
            await self.message.edit(embed=self.make_embed(game_over=True, winner=final_winner), view=None)
            active_games.pop(self.player1.id, None)
            self.stop()
            return

        self.choices = {}
        self.current_round += 1
        await self.message.edit(embed=self.make_embed(round_result=result_text))

    async def make_choice(self, interaction: Interaction, choice: str):
        if interaction.user not in [self.player1, self.player2] and not (self.vs_bot and interaction.user == self.player1):
            await interaction.response.send_message("❌ 你不是參加玩家！", ephemeral=True)
            return

        player_key = interaction.user if not self.vs_bot else self.player1

        if player_key in self.choices:
            await interaction.response.send_message("❌ 你已經出過拳了！", ephemeral=True)
            return

        self.choices[player_key] = choice
        await interaction.response.defer()

        expected = 2 if not self.vs_bot else 1
        current_choices = len(self.choices)
        if self.vs_bot and "bot" not in self.choices:
            current_choices = 1

        if current_choices >= expected:
            if self.vs_bot:
                self.choices["bot"] = random.choice(["✊", "✌️", "✋"])
            await self.handle_round()
        else:
            player_waiting = self.player2.mention if self.player2 else "另一位玩家"
            if self.player2 in self.choices:
                player_waiting = self.player1.mention
            await interaction.followup.send(f"✅ 你已選擇 **{choice}**。等待 {player_waiting} 出拳...", ephemeral=True)

# =========================
# Active games global
# =========================
active_games: Dict[int, RPSView] = {}

# =========================
# COGS (單檔案實作) — 每個 Cog 都以 class 定義並在 on_ready 加入
# 一些只含一個指令的 Cog（Help, Logs, Ping, ReactionRole）照你要求給完整 Cog
# =========================

# ---- HelpCog (/help) ----
class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="顯示所有可用的指令")
    async def help(self, interaction: Interaction):
        await log_command(interaction, "/help")
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        embed = discord.Embed(title="📖 指令清單", description="以下是目前可用的指令：", color=discord.Color.blue())
        for cmd in self.bot.tree.get_commands():
            if cmd.name in ("internal_command_to_hide",):
                continue
            embed.add_field(name=f"/{cmd.name}", value=cmd.description or "沒有描述", inline=False)

        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                print("Help: interaction expired")

# ---- LogsCog (/logs) ----
class LogsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="logs", description="在 Discord 訊息中顯示最近的指令紀錄")
    async def logs(self, interaction: Interaction):
        await log_command(interaction, "/logs")
        user_id = int(interaction.user.id)
        if user_id not in SPECIAL_USER_IDS and user_id not in LOG_VIEWER_IDS:
            try:
                await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            except Exception:
                pass
            return

        logs_text = "📜 **最近的指令紀錄**\n\n"
        if not COMMAND_LOGS:
            logs_text += "目前沒有任何紀錄。"
        else:
            logs_text += "\n".join([f"`{log['time']}`: {log['text']}" for log in COMMAND_LOGS[-10:]])
        try:
            await interaction.response.send_message(logs_text, ephemeral=True)
        except Exception:
            try:
                await interaction.followup.send(logs_text, ephemeral=True)
            except Exception:
                print("Logs: cannot respond")

# ---- PingCog (/ping) ----
class PingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="測試機器人是否在線")
    async def ping(self, interaction: Interaction):
        await log_command(interaction, "/ping")
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        latency_ms = round(self.bot.latency * 1000)
        try:
            await interaction.followup.send(f"🏓 Pong! **{latency_ms}ms**", ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(f"🏓 Pong! **{latency_ms}ms**", ephemeral=True)
            except Exception:
                pass

# ---- ReactionRoleCog (/reactionrole) ----
class ReactionRoleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reaction_roles: Dict[int, Dict[int, Dict[str, int]]] = {}  # guild_id -> message_id -> {emoji: role_id}

    @app_commands.command(name="reactionrole", description="新增反應身分組（管理員用）")
    async def reactionrole(self, interaction: Interaction, message: str, emoji: str, role: discord.Role, channel: Optional[discord.TextChannel] = None):
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
            channel = channel or interaction.channel
            async for msg in channel.history(limit=200):
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

# ---- UtilityCog (多指令) ----
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="say", description="讓機器人發送訊息（管理員或特殊使用者限定）")
    async def say(self, interaction: Interaction, message: str, channel: Optional[discord.TextChannel] = None, user: Optional[discord.User] = None):
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

    @app_commands.command(name="announce", description="發布公告（管理員限定）")
    async def announce(self, interaction: Interaction, content: str, title: Optional[str] = "公告📣", channel: Optional[discord.TextChannel] = None, ping_everyone: bool = False):
        await log_command(interaction, "/announce")
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("❌ 只有管理員能發布公告", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        embed = discord.Embed(title=title, description=content, color=discord.Color.orange())
        embed.set_footer(text=f"發布者：{interaction.user.display_name}")
        mention = "@everyone" if ping_everyone else ""
        await target_channel.send(content=mention, embed=embed)
        await interaction.followup.send(f"✅ 公告已發送到 {target_channel.mention}", ephemeral=True)

    @app_commands.command(name="calc", description="簡單計算器")
    async def calc(self, interaction: Interaction, expr: str):
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
    async def delete(self, interaction: Interaction, amount: int):
        await log_command(interaction, "/delete")
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.followup.send("❌ 只有管理員可以刪除訊息", ephemeral=True)
            return
        if amount < 1 or amount > 100:
            await interaction.followup.send("❌ 請輸入 1 ~ 100 的數字", ephemeral=True)
            return
        try:
            channel = interaction.channel
            deleted = await channel.purge(limit=amount + 1)
            await interaction.followup.send(f"✅ 已刪除 {len(deleted) - 1} 則訊息", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 刪除失敗: {e}", ephemeral=True)

# ---- ModerationCog ----
class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, interaction: Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用。", ephemeral=True)
            return False
        return True

    @app_commands.command(name="踢出", description="將成員踢出伺服器（需要權限）")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick_member(self, interaction: Interaction, member: discord.Member, reason: Optional[str] = "無"):
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
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban_member(self, interaction: Interaction, user_id: str, reason: Optional[str] = "無"):
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
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout_member(self, interaction: Interaction, member: discord.Member, duration: int, time_unit: str, reason: Optional[str] = "無"):
        await log_command(interaction, "/禁言")
        await interaction.response.defer(ephemeral=True)
        unit_seconds = {"秒": 1, "分鐘": 60, "小時": 3600, "天": 86400}
        if time_unit not in unit_seconds:
            await interaction.followup.send("❌ 時間單位錯誤。請使用 秒、分鐘、小時、天。", ephemeral=True)
            return
        timeout_seconds = duration * unit_seconds[time_unit]
        if timeout_seconds > 2419200:
            await interaction.followup.send("❌ 禁言時間不能超過 28 天。", ephemeral=True)
            return
        timeout = discord.utils.utcnow() + discord.timedelta(seconds=timeout_seconds) if hasattr(discord, "utils") else None
        try:
            # discord.Member.timeout expects a datetime.timedelta (discord.py >=2.0)
            await member.timeout(discord.timedelta(seconds=timeout_seconds), reason=reason)
            await interaction.followup.send(f"✅ 已禁言 {member.mention} {duration}{time_unit}。原因：`{reason}`")
        except discord.Forbidden:
            await interaction.followup.send("❌ 機器人沒有足夠的權限來禁言此成員。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 禁言失敗: {e}", ephemeral=True)

    @timeout_member.autocomplete('time_unit')
    async def time_unit_autocomplete(self, interaction: Interaction, current: str):
        units = ["秒", "分鐘", "小時", "天"]
        return [app_commands.Choice(name=unit, value=unit) for unit in units if current.lower() in unit]

    @app_commands.command(name="解除禁言", description="解除成員的禁言狀態")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout_member(self, interaction: Interaction, member: discord.Member):
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

# ---- FunCog (遊戲/實用指令) ----
class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="gay", description="測試一個人的隨機同性戀機率 (1-100%)")
    async def gay_probability(self, interaction: Interaction, user: Optional[discord.User] = None):
        await log_command(interaction, "/gay")
        target_user = user if user else interaction.user
        if target_user.id in HUNDRED_PERCENT_IDS:
            probability = 100
        elif target_user.id in SPECIAL_USER_IDS:
            probability = 0
        else:
            probability = random.randint(1, 100)
        embed = discord.Embed(title="🏳️‍🌈 隨機同性戀機率 (/gay)", color=discord.Color.random())
        embed.add_field(name="測試者", value=target_user.mention, inline=False)
        embed.add_field(name="機率為", value=f"**{probability}%**", inline=False)
        embed.set_footer(text=f"由 {interaction.user.display_name} 執行")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="rps", description="剪刀石頭布對戰")
    async def rps(self, interaction: Interaction, rounds: int = 3, opponent: Optional[discord.User] = None, vs_bot: bool = False):
        await log_command(interaction, "/rps")
        await interaction.response.defer()
        if not opponent and not vs_bot:
            await interaction.followup.send("❌ 你必須選擇對手或開啟 vs_bot!", ephemeral=True)
            return
        if opponent and opponent.bot:
            await interaction.followup.send("🤖 不能邀請機器人，請改用 vs_bot=True", ephemeral=True)
            return
        if interaction.user.id in active_games:
            await interaction.followup.send("❌ 你已經在一場 RPS 遊戲中！請先完成或取消它。", ephemeral=True)
            return
        if opponent and opponent.id in active_games:
            await interaction.followup.send("❌ 你的對手已經在一場 RPS 遊戲中！", ephemeral=True)
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
        active_games[interaction.user.id] = view

    @app_commands.command(name="氣泡紙", description="發送一個巨大的氣泡紙，來戳爆它吧！")
    async def bubble_wrap_command(self, interaction: Interaction):
        await log_command(interaction, "/氣泡紙")
        bubble = "||啪|| " * 200
        await interaction.response.send_message(f"點擊這些氣泡來戳爆它們！\n{bubble}")

    @app_commands.command(name="dice", description="擲一顆 1-6 的骰子")
    async def dice(self, interaction: Interaction):
        await log_command(interaction, "/dice")
        number = random.randint(1, 6)
        await interaction.response.send_message(f"🎲 {interaction.user.mention} 擲出了 **{number}**！")

    @app_commands.command(name="抽籤", description="在多個選項中做出隨機決定。選項之間用逗號（,）分隔")
    async def choose(self, interaction: Interaction, options: str):
        await log_command(interaction, "/抽籤")
        choices = [opt.strip() for opt in options.split(',') if opt.strip()]
        if len(choices) < 2:
            await interaction.response.send_message("❌ 請提供至少兩個選項，並用逗號 (,) 分隔。", ephemeral=True)
            return
        selected = random.choice(choices)
        embed = discord.Embed(title="🎯 抽籤結果", description=f"我在以下選項中抽了一個：\n`{options}`", color=discord.Color.green())
        embed.add_field(name="🎉 最終選擇", value=f"**{selected}**", inline=False)
        embed.set_footer(text=f"決定者：{interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

# =========================
# VoiceCog (音樂簡化版)
# - 提供 /play 基本功能、隊列、簡單控制面板
# - 為了穩定，將 yt-dlp 抽取與 FFmpeg 播放做 try/except 處理
# =========================

# =========================
# VoiceCog 與 MusicControlView
# =========================

from discord.ext import commands
from discord import app_commands, Interaction
from discord import FFmpegPCMAudio
import discord
import asyncio
import functools
from typing import Optional
from yt_dlp import YoutubeDL

# Helper：安全取得語音物件
async def get_voice_client(interaction: Interaction) -> Optional[discord.VoiceClient]:
    if not interaction.guild:
        await interaction.followup.send("❌ 這個指令只能在伺服器中使用。", ephemeral=True)
        return None
    return interaction.guild.voice_client

# ------------------------------
# MusicControlView
# ------------------------------
class MusicControlView(discord.ui.View):
    def __init__(self, cog, guild_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        vc = await get_voice_client(interaction)
        if not vc:
            return interaction.user.guild_permissions.administrator
        if interaction.user.voice and interaction.user.voice.channel == vc.channel:
            return True
        return interaction.user.guild_permissions.administrator

    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.primary)
    async def pause_resume_button(self, interaction: Interaction, button: discord.ui.Button):
        guild_id = self.guild_id
        vc = self.cog.vc_dict.get(guild_id)
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            await interaction.response.send_message("❌ 目前沒有播放中的音樂。", ephemeral=True)
            return
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ 暫停播放", ephemeral=True)
        else:
            vc.resume()
            await interaction.response.send_message("▶️ 繼續播放", ephemeral=True)
        await self.cog.update_control_message(guild_id)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: Interaction, button: discord.ui.Button):
        guild_id = self.guild_id
        vc = self.cog.vc_dict.get(guild_id)
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            await interaction.response.send_message("❌ 目前沒有播放中的音樂。", ephemeral=True)
            return
        skipped_title = self.cog.now_playing.get(guild_id, ("當前歌曲", 0, 0))[0]
        vc.stop()
        await interaction.response.send_message(f"⏩ 已跳過 **{skipped_title}**。", ephemeral=True)

    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: Interaction, button: discord.ui.Button):
        guild_id = self.guild_id
        vc = self.cog.vc_dict.get(guild_id)
        if vc and vc.is_connected():
            vc.stop()
            await vc.disconnect()
            self.cog.queue.pop(guild_id, None)
            self.cog.now_playing.pop(guild_id, None)
            self.cog.current_volume.pop(guild_id, None)
            self.cog.vc_dict.pop(guild_id, None)
            await interaction.response.send_message("⏹️ 已停止播放並離開語音頻道", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 目前沒有連線的語音頻道。", ephemeral=True)
        await self.cog.update_control_message(guild_id)

    @discord.ui.button(label="🔊 +", style=discord.ButtonStyle.success)
    async def volume_up_button(self, interaction: Interaction, button: discord.ui.Button):
        guild_id = self.guild_id
        vc = self.cog.vc_dict.get(guild_id)
        if not vc:
            await interaction.response.send_message("❌ 機器人不在語音頻道", ephemeral=True)
            return
        current_vol = self.cog.current_volume.get(guild_id, 0.5)
        new_vol = min(1.0, current_vol + 0.25)
        self.cog.current_volume[guild_id] = new_vol
        if vc.source:
            vc.source.volume = new_vol
        await interaction.response.send_message(f"🔊 音量已調整為 {int(new_vol*100)}%", ephemeral=True)
        await self.cog.update_control_message(guild_id)

    @discord.ui.button(label="🔇 -", style=discord.ButtonStyle.danger)
    async def volume_down_button(self, interaction: Interaction, button: discord.ui.Button):
        guild_id = self.guild_id
        vc = self.cog.vc_dict.get(guild_id)
        if not vc:
            await interaction.response.send_message("❌ 機器人不在語音頻道", ephemeral=True)
            return
        current_vol = self.cog.current_volume.get(guild_id, 0.5)
        new_vol = max(0.0, current_vol - 0.25)
        self.cog.current_volume[guild_id] = new_vol
        if vc.source:
            vc.source.volume = new_vol
        await interaction.response.send_message(f"🔇 音量已調整為 {int(new_vol*100)}%", ephemeral=True)
        await self.cog.update_control_message(guild_id)


# ------------------------------
# VoiceCog
# ------------------------------
class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}             # {guild_id: [(audio_url, title, duration), ...]}
        self.now_playing = {}       # {guild_id: (title, duration, start_time)}
        self.vc_dict = {}           # {guild_id: voice_client}
        self.current_volume = {}    # {guild_id: float}
        self.control_messages = {}  # {guild_id: message_id}

    # --------------------
    # yt-dlp 音訊提取
    # --------------------
    async def extract_yt_dlp(self, url: str):
        ydl_opts = {'format': 'bestaudio/best', 'quiet': True, 'noplaylist': True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            audio_url = info.get('url')
            title = info.get('title', '未知曲目')
            duration = info.get('duration', 0)
            return audio_url, title, duration

    # --------------------
    # 播放控制
    # --------------------
    async def start_playback(self, guild_id):
        lock = getattr(self, f"_lock_{guild_id}", None)
        if not lock:
            lock = asyncio.Lock()
            setattr(self, f"_lock_{guild_id}", lock)
        async with lock:
            q = self.queue.get(guild_id)
            vc = self.vc_dict.get(guild_id)
            if not q or not vc or vc.is_playing() or vc.is_paused():
                await self.update_control_message(guild_id)
                return

            audio_url, title, duration = q.pop(0)
            self.now_playing[guild_id] = (title, duration, asyncio.get_event_loop().time())
            await self.update_control_message(guild_id)

            try:
                current_vol = self.current_volume.setdefault(guild_id, 0.5)
                source = FFmpegPCMAudio(
                    audio_url,
                    executable='/usr/bin/ffmpeg',
                    before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                    options="-vn"
                )
                source = discord.PCMVolumeTransformer(source, volume=current_vol)
                callback = functools.partial(self.player_after_callback, guild_id)
                vc.play(source, after=callback)
            except Exception as e:
                print(f"❌ 嘗試播放 {title} 發生錯誤: {e}")
                await self.player_after_callback(guild_id, e)

    async def player_after_callback(self, guild_id, error):
        vc = self.vc_dict.get(guild_id)
        if error:
            print(f"播放錯誤: {error}")
        self.now_playing.pop(guild_id, None)
        await self.update_control_message(guild_id)
        if self.queue.get(guild_id):
            await self.start_playback(guild_id)
        else:
            if vc and vc.is_connected():
                await vc.disconnect()
                self.vc_dict.pop(guild_id, None)
                self.control_messages.pop(guild_id, None)
                self.current_volume.pop(guild_id, None)

    # --------------------
    # 控制面板訊息更新
    # --------------------
    async def update_control_message(self, guild_id: int, channel: discord.TextChannel = None):
        vc = self.vc_dict.get(guild_id)
        q = self.queue.get(guild_id, [])
        now_playing_info = self.now_playing.get(guild_id)
        view = MusicControlView(self, guild_id)

        target_channel = channel
        if not target_channel and vc and vc.channel.guild.text_channels:
            target_channel = vc.channel.guild.text_channels[0]

        if not target_channel:
            return

        embed = discord.Embed(title="🎶 音樂播放器", color=discord.Color.blue())
        status_text = "目前無播放"
        if vc and vc.is_playing():
            status_text = "▶️ 播放中"
        elif vc and vc.is_paused():
            status_text = "⏸️ 已暫停"
        elif vc and not vc.is_playing() and q:
            status_text = "🔃 即將播放"
        embed.add_field(name="狀態", value=status_text, inline=False)

        if now_playing_info:
            title, total_duration, _ = now_playing_info
            vol_percent = int(self.current_volume.get(guild_id, 0.5) * 100)
            embed.add_field(
                name="現在播放",
                value=f"**{title}** (`{total_duration}s`) 音量: {vol_percent}%",
                inline=False
            )
        else:
            embed.add_field(name="現在播放", value="無", inline=False)

        if q:
            queue_text = "\n".join([f"{i+1}. {info[1]} (`{info[2]}s`)" for i, info in enumerate(q[:10])])
            embed.add_field(name=f"即將播放 ({len(q)} 首)", value=queue_text, inline=False)
        else:
            embed.add_field(name="隊列", value="隊列是空的", inline=False)

        try:
            msg_id = self.control_messages.get(guild_id)
            if msg_id:
                msg = await target_channel.fetch_message(msg_id)
                await msg.edit(embed=embed, view=view)
            else:
                msg = await target_channel.send(embed=embed, view=view)
                self.control_messages[guild_id] = msg.id
        except discord.NotFound:
            msg = await target_channel.send(embed=embed, view=view)
            self.control_messages[guild_id] = msg.id
        except Exception as e:
            print(f"更新控制訊息失敗: {e}")

    # --------------------
    # /play 指令
    # --------------------
    @app_commands.command(name="play", description="播放 YouTube 音樂或搜尋歌曲")
    @app_commands.describe(query="歌曲連結或關鍵字")
    async def play(self, interaction: Interaction, query: str):
        await interaction.response.defer(ephemeral=False)
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 你必須先加入語音頻道", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        guild_id = interaction.guild.id

        vc = interaction.guild.voice_client
        if not vc:
            vc = await channel.connect()
        elif vc.channel != channel:
            await vc.move_to(channel)
        self.vc_dict[guild_id] = vc

        ydl_opts = {'format': 'bestaudio/best', 'quiet': True, 'noplaylist': True, 'default_search': 'ytsearch1'}
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                audio_url = info['url']
                title = info.get('title', '未知曲目')
                duration = info.get('duration', 0)
        except Exception as e:
            await interaction.followup.send(f"❌ 取得音訊失敗: {e}", ephemeral=True)
            return

        q = self.queue.setdefault(guild_id, [])
        q.append((audio_url, title, duration))
        await self.update_control_message(guild_id, interaction.channel)

        if not vc.is_playing() and not vc.is_paused():
            asyncio.create_task(self.start_playback(guild_id))

        await interaction.followup.send(f"✅ **{title}** 已加入隊列！", ephemeral=True)
# =========================
# SupportCog: 私訊轉發管理（包含 ServerSelectView）
# =========================

class ServerSelectView(ui.View):
    def __init__(self, bot: commands.Bot, user_id: int, cog):
        super().__init__(timeout=None)
        self.bot = bot
        self.user_id = user_id
        self.cog = cog
        # build select on demand
        self.server_select = ui.Select(placeholder="載入伺服器...", options=[])
        self.server_select.custom_id = f"support_select_{user_id}"
        self.server_select.callback = self._on_select
        self.add_item(self.server_select)
        self.reset_button = ui.Button(label="重新選擇", style=discord.ButtonStyle.secondary, custom_id=f"support_reset_{user_id}")
        self.reset_button.callback = self._on_reset
        self.reset_button.disabled = True
        self.add_item(self.reset_button)
        self._load_options()

    def _load_options(self):
        self.server_select.options.clear()
        user = self.bot.get_user(self.user_id)
        if not user:
            self.server_select.placeholder = "載入中..."
            self.server_select.disabled = True
            return
        shared_guilds = [g for g in self.bot.guilds if g.get_member(self.user_id) is not None]
        if not shared_guilds:
            self.server_select.placeholder = "❌ 找不到共享伺服器"
            self.server_select.disabled = True
            return
        options = []
        for guild in shared_guilds:
            label = guild.name
            options.append(discord.SelectOption(label=label, value=str(guild.id)))
        self.server_select.options = options
        self.server_select.placeholder = "請選擇您要發送問題的伺服器"
        self.server_select.disabled = False

    async def _on_select(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        selected = int(self.server_select.values[0])
        if selected not in self.cog.support_config:
            await interaction.followup.send("❌ 該伺服器尚未設定轉發頻道，請選擇其他伺服器（或請管理員先設定）。", ephemeral=True)
            return
        self.cog.user_target_guild[self.user_id] = selected
        # save async
        asyncio.create_task(self.cog.save_state_async())
        self.server_select.disabled = True
        self.reset_button.disabled = False
        try:
            await interaction.message.edit(embed=discord.Embed(title="✅ 設定成功！", description=f"你已選擇：{self.bot.get_guild(selected).name}", color=discord.Color.green()), view=self)
            await interaction.followup.send("伺服器已設定，您現在可以直接輸入問題。", ephemeral=True)
        except Exception:
            await interaction.followup.send("✅ 已設定（無法更新界面）", ephemeral=True)

    async def _on_reset(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        self.cog.user_target_guild.pop(self.user_id, None)
        asyncio.create_task(self.cog.save_state_async())
        self.server_select.disabled = False
        self.reset_button.disabled = True
        self._load_options()
        try:
            await interaction.message.edit(embed=discord.Embed(title="請選擇伺服器", description="請重新選擇您要聯繫的伺服器。", color=discord.Color.blue()), view=self)
            await interaction.followup.send("✅ 已重置，請重新選擇。", ephemeral=True)
        except Exception:
            await interaction.followup.send("✅ 已重置（無法更新界面）", ephemeral=True)

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.user_id

class SupportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.support_config: Dict[int, Tuple[int, Optional[int]]] = {}  # guild_id -> (channel_id, role_id)
        self.user_target_guild: Dict[int, int] = {}
        self.config_file = "support_config.json"
        self.load_support_config()

    def load_support_config(self):
        try:
            with open(self.config_file, "r") as f:
                data = json.load(f)
                self.support_config = {int(k): tuple(v) for k, v in data.get("channels", {}).items()}
                self.user_target_guild = {int(k): v for k, v in data.get("targets", {}).items()}
        except FileNotFoundError:
            self.support_config = {}
        except Exception as e:
            print("load_support_config error:", e)

    def save_support_config(self):
        try:
            data = {"channels": {str(k): list(v) for k, v in self.support_config.items()}, "targets": {str(k): v for k, v in self.user_target_guild.items()}}
            with open(self.config_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print("save_support_config error:", e)

    async def save_state_async(self):
        await asyncio.to_thread(self.save_support_config)

    @app_commands.command(name="set_support_channel", description="[管理員] 設定用戶問題轉發頻道與通知角色")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_support_channel(self, interaction: Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 此指令只能在伺服器頻道中使用。", ephemeral=True)
            return
        guild_id = interaction.guild.id
        role_id = role.id if role else None
        self.support_config[guild_id] = (channel.id, role_id)
        await self.save_state_async()
        notification_text = f"通知角色：{role.mention}" if role else "無通知角色。"
        embed = discord.Embed(title="✅ 問題轉發設定成功", description=f"伺服器 **{interaction.guild.name}** 的用戶問題將會被轉發到 {channel.mention}。\n\n{notification_text}", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="support", description="在私訊中手動呼叫伺服器選擇選單")
    async def support_command(self, interaction: Interaction):
        if interaction.guild is not None or not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ 這個指令只能在和機器人的私訊頻道中使用。", ephemeral=True)
            return
        user_id = interaction.user.id
        if self.user_target_guild.get(user_id):
            target_guild = bot.get_guild(self.user_target_guild[user_id])
            if target_guild:
                await interaction.response.send_message(f"✅ 您目前已設定將問題轉發至 **{target_guild.name}**。請直接輸入您的問題。", ephemeral=True)
                return
        view = ServerSelectView(bot, user_id, self)
        try:
            await interaction.response.send_message(embed=discord.Embed(title="選擇要聯繫管理員的伺服器", description="請從下方的下拉選單中選擇您要發送問題的伺服器。", color=discord.Color.blue()), view=view, ephemeral=True)
        except Exception:
            await interaction.response.send_message("請私訊管理員以取得協助（無法顯示介面）。", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return
        user_id = message.author.id
        target_guild_id = self.user_target_guild.get(user_id)
        if target_guild_id:
            target_guild = bot.get_guild(target_guild_id)
            if target_guild:
                await self.process_forward(message.author, message.content, str(target_guild_id))
            else:
                self.user_target_guild.pop(user_id, None)
                asyncio.create_task(self.save_state_async())
                await message.channel.send("❌ 您之前選擇的伺服器無效或機器人已退出，請重新發送訊息來設置。")
        else:
            view = ServerSelectView(bot, user_id, self)
            try:
                await message.channel.send(embed=discord.Embed(title="選擇要聯繫管理員的伺服器", description="請選擇。", color=discord.Color.blue()), view=view)
            except Exception:
                await message.channel.send("❌ 處理您的請求失敗，請稍後再試。")

    async def process_forward(self, user: discord.User, question: str, guild_id_str: str):
        target_guild_id = int(guild_id_str)
        target_guild = bot.get_guild(target_guild_id)
        config_data = self.support_config.get(target_guild_id)
        if not config_data:
            self.user_target_guild.pop(user.id, None)
            asyncio.create_task(self.save_state_async())
            await user.send(f"❌ 伺服器 ID {target_guild_id} 尚未設定管理頻道，請重新選擇伺服器。")
            return
        support_channel_id, role_id = config_data
        target_channel = target_guild.get_channel(support_channel_id) if target_guild else None
        if not target_channel or not isinstance(target_channel, discord.TextChannel):
            self.user_target_guild.pop(user.id, None)
            asyncio.create_task(self.save_state_async())
            await user.send(f"❌ 設定的頻道無效或已被刪除，請重新選擇伺服器。")
            return
        message_content = f"<@&{role_id}>：有新的用戶問題" if role_id else f"**<@{target_guild.owner_id}> 或管理員注意：有新的用戶問題**"
        embed = discord.Embed(title=f"❓ 來自 {user.name} 的問題", description=f"**發送者:** <@{user.id}> ({user.name}#{user.discriminator})\n**伺服器:** `{target_guild.name}` ({target_guild_id})\n\n**訊息內容:**\n```\n{question}\n```", color=discord.Color.gold())
        embed.set_footer(text="請點擊下方按鈕進行回覆或標記為已處理。")
        view = ReplyView(user.id, question, self)
        # Detect first URL
        match = re.search(r"(https?://[^\s]+)", question)
        if match:
            first_url = match.group(0).strip()
            view.add_item(ui.Button(label="🔗 打開用戶提供的連結", style=discord.ButtonStyle.link, url=first_url))
        try:
            await target_channel.send(content=message_content, embed=embed, view=view)
        except discord.Forbidden:
            await user.send("❌ 機器人沒有權限在該伺服器的管理頻道發送訊息。")
        except Exception as e:
            await user.send(f"❌ 轉發時發生未知錯誤: {e}")

# =========================
# Error handling
# =========================

@bot.tree.error
async def on_app_command_error(interaction: Interaction, error):
    # If already responded, use followup
    msg = None
    try:
        if interaction.response.is_done():
            if isinstance(error, app_commands.MissingPermissions):
                msg = f"❌ 權限不足：你缺少 {', '.join(error.missing_permissions)}"
            elif isinstance(error, app_commands.CheckFailure):
                msg = str(error)
            else:
                print("Unhandled command error:", type(error).__name__, error)
                msg = f"❌ 指令錯誤：{error}"
            await interaction.followup.send(msg, ephemeral=True)
            return
    except Exception:
        pass
    # if not responded
    if isinstance(error, app_commands.MissingPermissions):
        msg = f"❌ 權限不足：你缺少 {', '.join(error.missing_permissions)}"
    elif isinstance(error, app_commands.CheckFailure):
        msg = str(error)
    else:
        print("Unhandled command error:", type(error).__name__, error)
        msg = f"❌ 指令錯誤：{error}"
    try:
        await interaction.response.send_message(msg, ephemeral=True)
    except discord.errors.NotFound:
        print("Error handling failed: interaction not found")

# =========================
# on_ready: load cogs and sync once
# =========================

@bot.event
async def on_ready():
    global discord_loop
    if getattr(bot, "_has_ready_run", False):
        return
    bot._has_ready_run = True
    try:
        discord_loop = asyncio.get_running_loop()
    except Exception:
        discord_loop = None
    print(f"[{safe_now()}] Bot logged in as {bot.user} ({bot.user.id})")

    # add cogs (instantiate and add)
    try:
        bot.add_cog(HelpCog(bot))
        bot.add_cog(LogsCog(bot))
        bot.add_cog(PingCog(bot))
        bot.add_cog(ReactionRoleCog(bot))
        bot.add_cog(UtilityCog(bot))
        bot.add_cog(ModerationCog(bot))
        bot.add_cog(FunCog(bot))
        bot.add_cog(SupportCog(bot))
        bot.add_cog(VoiceCog(bot))
    except Exception as e:
        print("Cog add error:", e)
        traceback.print_exc()

    # register persistent views if needed
    try:
        # Support views
        support_cog = bot.get_cog("SupportCog")
        if support_cog:
            bot.add_view(ReplyView(0, "", support_cog))
            # ServerSelectView needs user-specific instances, we don't add global one here
    except Exception:
        pass

    # set presence
    try:
        await bot.change_presence(status=discord.Status.online, activity=discord.Game(name="服務中 | /help"))
    except Exception:
        pass

    # sync commands once
    try:
        await bot.tree.sync()
        print("✅ Commands synced.")
    except Exception as e:
        print("❌ Failed to sync commands:", e)

# =========================
# ⚡ Flask Web 部分
# =========================
from flask import Flask, render_template, session, redirect, url_for, request, jsonify
import asyncio
import requests
import os

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change_this_to_secure_key")

# Discord OAuth2 設定
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DISCORD_API_BASE_URL = "https://discord.com/api/v10"
TOKEN_URL = f"{DISCORD_API_BASE_URL}/oauth2/token"
USER_URL = f"{DISCORD_API_BASE_URL}/users/@me"

# 權限設定
ADMINISTRATOR_PERMISSION = 0x8
SPECIAL_USER_IDS = []  # 你可以放特定管理員ID
LOG_VIEWER_IDS = []    # 可看日誌的使用者ID

# Discord 事件循環（bot.py中會設置）
discord_loop = None
bot = None  # 由你的 bot.py 設定全域 bot 實例

# 命令日誌
command_logs = []

# --------------------------
# OAuth2 登入頁面
# --------------------------
AUTH_URL = (
    f"https://discord.com/api/oauth2/authorize"
    f"?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}"
    f"&response_type=code&scope=identify%20guilds%20guilds.members.read"
)

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

    return render_template(
        'dashboard.html',
        user=user_data,
        guilds=filtered_guilds,
        is_special_user=is_special_user,
        DISCORD_CLIENT_ID=DISCORD_CLIENT_ID
    )

# --------------------------
# 伺服器儀表板
# --------------------------
@app.route("/guild/<int:guild_id>")
def guild_dashboard(guild_id):
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    if not user_data or not guilds_data:
        return redirect(url_for('index'))

    guild_found = any(
        (int(g['id']) == guild_id and (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION)
        for g in guilds_data
    )
    if not guild_found:
        return "❌ 權限不足：你沒有權限管理這個伺服器。", 403

    global discord_loop
    if discord_loop is None or not discord_loop.is_running():
        return "❌ 內部錯誤：Discord 機器人事件循環尚未啟動。", 500

    if not bot.get_guild(guild_id):
        return f"❌ 錯誤：找不到伺服器 ID **{guild_id}**。請確認機器人已加入此伺服器。", 404

    return redirect(url_for('settings', guild_id=guild_id))

# --------------------------
# 伺服器設定
# --------------------------
@app.route("/guild/<int:guild_id>/settings", methods=['GET', 'POST'])
@app.route("/guild/<int:guild_id>/settings/<string:module>", methods=['GET', 'POST'])
def settings(guild_id, module=None):
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    if not user_data or not guilds_data:
        return redirect(url_for('index'))

    guild_found = any(
        (int(g['id']) == guild_id and (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION)
        for g in guilds_data
    )
    if not guild_found:
        return "❌ 你沒有權限管理這個伺服器", 403

    global discord_loop
    if discord_loop is None or not discord_loop.is_running():
        return "❌ 內部錯誤：Discord 機器人事件循環尚未啟動。", 500

    guild_obj = bot.get_guild(guild_id)
    if not guild_obj:
        return "❌ 機器人不在這個伺服器或連線超時。", 404

    config = load_config(guild_id)  # 你自訂的設定讀取函式

    if request.method == 'POST':
        if module == 'notifications':
            config['welcome_channel_id'] = request.form.get('welcome_channel_id', '')
            config['video_notification_channel_id'] = request.form.get('video_channel_id', '')
            config['video_notification_message'] = request.form.get('video_message', '')
            config['live_notification_message'] = request.form.get('live_message', '')
            save_config(guild_id, config)  # 你自訂的設定存檔函式
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

    if module == 'notifications':
        return render_template('settings_notifications.html', **context)
    else:
        return render_template('settings_main.html', **context)

# --------------------------
# 成員列表
# --------------------------
@app.route("/guild/<int:guild_id>/members")
def members_page(guild_id):
    user_data = session.get("discord_user")
    guilds_data = session.get("discord_guilds")
    if not user_data or not guilds_data:
        return redirect(url_for('index'))

    guild_found = any(
        (int(g['id']) == guild_id and (int(g.get('permissions', '0')) & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION)
        for g in guilds_data
    )
    if not guild_found:
        return "❌ 你沒有權限管理這個伺服器", 403

    global discord_loop
    if discord_loop is None or not discord_loop.is_running():
        return "❌ 內部錯誤：Discord 機器人事件循環尚未啟動。", 500

    try:
        guild_obj = bot.get_guild(guild_id)
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
        return f"❌ Discord 存取錯誤：請檢查機器人是否開啟 **SERVER MEMBERS INTENT** 且擁有伺服器管理權限。錯誤訊息: {e}", 500
    except TimeoutError:
        return f"❌ 內部伺服器錯誤：獲取成員清單超時（>10 秒）。", 500
    except Exception as e:
        return f"❌ 內部伺服器錯誤：在處理成員資料時發生意外錯誤。錯誤訊息: {e}", 500

# --------------------------
# 通知模態
# --------------------------
@app.route("/guild/<int:guild_id>/settings/notifications_modal", methods=['GET'])
def notifications_modal(guild_id):
    global discord_loop
    if discord_loop is None or not discord_loop.is_running():
        return "❌ 載入設定失敗！錯誤：Discord 機器人事件循環尚未啟動。", 500

    try:
        async def fetch_and_prepare_data():
            guild_obj = bot.get_guild(guild_id)
            if guild_obj is None:
                raise ValueError(f"找不到伺服器 ID {guild_id}。機器人可能已離開或 ID 無效。") 
            channels = guild_obj.text_channels
            config = load_config(guild_id)
            video_channel_id = str(config.get('video_notification_channel_id', ''))
            video_message = config.get('video_notification_message', 'New Video from {channel}: {title}\n{link}')
            live_message = config.get('live_notification_message', '@everyone {channel} is Live! {title}\n{link}')
            ping_role = config.get('ping_role', '')
            content_filter = config.get('content_filter', 'Videos,Livestreams')
            return {
                'guild_obj': guild_obj,
                'channels': channels,
                'video_channel_id': video_channel_id,
                'video_message': video_message,
                'live_message': live_message,
                'ping_role': ping_role,
                'content_filter': content_filter
            }

        future = asyncio.run_coroutine_threadsafe(fetch_and_prepare_data(), discord_loop)
        data = future.result(timeout=5)
        return render_template('modal_notifications.html', **data)

    except ValueError as ve:
        return f"❌ 載入設定失敗！錯誤：{str(ve)}", 404
    except discord.NotFound:
        return f"❌ 載入設定失敗！錯誤：找不到伺服器 ID **{guild_id}**。請確認機器人已加入此伺服器。", 404
    except TimeoutError:
        return f"❌ 載入設定失敗！錯誤：與 Discord API 連線超時（>5 秒）。", 500
    except Exception as e:
        return f"❌ 載入設定失敗！錯誤：在處理資料時發生意外錯誤。", 500

# --------------------------
# 日誌
# --------------------------
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

# --------------------------
# 服務條款與隱私
# --------------------------
@app.route("/terms")
def terms_of_service():
    return render_template('terms_of_service.html')

@app.route("/privacy")
def privacy_policy():
    return render_template('privacy_policy.html')

# --------------------------
# Discord OAuth2 Callback
# --------------------------
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
    try:
        token_response.raise_for_status()
    except requests.HTTPError as e:
        return f"授權失敗: {e.response.text}", 400

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

# --------------------------
# 登出
# --------------------------
@app.route("/logout")
def logout():
    session.pop("discord_user", None)
    session.pop("discord_guilds", None)
    return redirect(url_for("index"))

# =========================
# ⚡ 執行區塊 (修正版)
# =========================
bot = None


def run_web():
    port = os.getenv("PORT", 8080)
    # Render 或其他平台不適合 debug=True, use_reloader=True
    app.run(host="0.0.0.0", port=int(port), debug=False, use_reloader=False)

def keep_web_alive():
    """在背景執行 Flask"""
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

async def start_bot():
    """啟動 Discord bot"""
    global discord_loop
    discord_loop = asyncio.get_running_loop()
    print("啟動 Discord Bot...")
    try:
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        print("機器人已手動關閉。")
    except Exception as e:
        print(f"Discord Bot 啟動錯誤: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    # 1️⃣ 啟動 Flask Web 服務 (背景線程)
    keep_web_alive()
    print("Flask Web 已啟動於背景線程。")

    # 2️⃣ 啟動 Discord Bot
    # 使用 asyncio.run 確保全局 event loop
    try:
        asyncio.run(start_bot())
    except RuntimeError as e:
        # 常見錯誤處理
        if "Event loop is closed" in str(e) or "cannot run from a thread" in str(e):
            print("⚠️ Event loop 已關閉或不可從當前線程啟動。")
        else:
            raise