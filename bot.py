import os
import sys
import re
import json
import time
import random
import threading
import asyncio
import traceback
import discord
import logging # 用於記錄錯誤
from cryptography.fernet import Fernet
from typing import Optional, List, Dict, Tuple, Literal 
from utils import load_config, save_config 
from utils import safe_now
from utils import load_support_config, save_support_config
import discord
from discord.ext import commands, tasks
from discord import app_commands, ui, Interaction, TextChannel
from flask import Flask, session, request, render_template, redirect, url_for, jsonify
import tempfile
import uuid  # <--- 請新增這一行在最上面
import traceback
import tempfile
from cryptography.fernet import Fernet # 新增
import json # 新增
import io # 新增
import base64 # 新增
from typing import Optional, TYPE_CHECKING
import logging
import glob
from discord import app_commands, Interaction, ui
from discord.ext.commands import Context
from discord import FFmpegPCMAudio 

from cogs.backup_system import BackupSystem, RestorePreCheckView

from youtube_dl import YoutubeDL 

from typing import Optional, Dict, List, Tuple, Literal

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
TEMP_UPLOAD_FOLDER = 'static/temp_uploads' 
TARGET_CHANNEL_ID = "1446781237422198855" 

if not TOKEN:
    print("❌ DISCORD_TOKEN not set. Exiting.")
    sys.exit(1)

# =========================
# Utils & Shared State
# =========================
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
SPECIAL_USER_IDS = [1238436456041676853]
LOG_VIEWER_IDS = [1238436456041676853]
HUNDRED_PERCENT_IDS = [1343900739407319070,1227927780231090177]
SIXTY_NINE_IDS = [1358791121697898548] 
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
# --- 1. 定義還原確認的 Discord 介面 (Modal) ---


# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BackupSystem")


     


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

import os
import discord
from discord.ext import commands
from discord import app_commands
import glob 
import random

# 假設 TARGET_CHANNEL_ID 和 TEMP_UPLOAD_FOLDER 已經在檔案中定義

class ImageDrawCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 確保在初始化時可以讀取這些全局變數
        self.TARGET_CHANNEL_ID = TARGET_CHANNEL_ID
        self.TEMP_UPLOAD_FOLDER = TEMP_UPLOAD_FOLDER
        
    @app_commands.command(name="隨機抽圖", description="從圖庫中隨機抽取一張圖片發送。")
    async def draw_image(self, interaction: discord.Interaction):
        # 0. 先告訴 Discord 我們收到指令了 (避免超時)
        await interaction.response.defer()
        
        # 1. 檢查頻道設定
        channel = self.bot.get_channel(int(self.TARGET_CHANNEL_ID))
        if not channel:
            return await interaction.followup.send("❌ 錯誤：找不到指定的圖庫頻道 ID。", ephemeral=True)

        # 2. 收集所有圖片來源
        image_sources = []
        
        # --- 來源 A: 本地暫存資料夾 ---
        # 確保資料夾存在
        if os.path.exists(self.TEMP_UPLOAD_FOLDER):
            search_path = os.path.join(self.TEMP_UPLOAD_FOLDER, '*')
            # 過濾出真正的檔案，排除資料夾
            local_files = [f for f in glob.glob(search_path) if os.path.isfile(f)]
            
            for f in local_files:
                image_sources.append({
                    'type': 'LOCAL',
                    'path': f,
                    'name': os.path.basename(f)
                })
        
        # --- 來源 B: Discord 頻道歷史 ---
        try:
            # 讀取最近 200 條訊息 (數量少一點比較快)
            async for msg in channel.history(limit=200):
                if msg.attachments:
                    att = msg.attachments[0]
                    # 嚴格檢查：必須有 content_type 且是圖片
                    if att.content_type and att.content_type.startswith('image/'):
                        image_sources.append({
                            'type': 'DISCORD',
                            'url': att.url,
                            'name': att.filename or "unknown.png" # 防止檔名為 None
                        })
        except Exception as e:
            print(f"⚠️ 讀取歷史錯誤 (非致命): {e}")

        # 3. 如果完全沒圖
        if not image_sources:
            return await interaction.followup.send("❌ 圖庫目前空空如也 (暫存區與頻道皆無圖片)。", ephemeral=True)

        # 4. 隨機抽一張
        pick = random.choice(image_sources)

        # 5. 構建 Embed 與發送 (關鍵修正：邏輯完全分離)
        try:
            # --- 情況一：抽到本地圖片 ---
            if pick['type'] == 'LOCAL':
                file_name = pick['name']
                file_path = pick['path']
                
                # 建立 Embed
                embed = discord.Embed(
                    title="🖼️ 隨機圖庫圖片",
                    description="這是從雲端圖庫中隨機挑選的精彩照片！",
                    color=discord.Color.orange() # 用不同顏色區分本地
                )
                # 本地圖片必須用 attachment:// 語法
                embed.set_image(url=f"attachment://{file_name}")
                embed.set_footer(text=f"來源: 暫存區 | 檔名: {file_name}")
                
                # 建立檔案物件
                discord_file = discord.File(file_path, filename=file_name)
                
                # 發送 (帶上 file)
                await interaction.followup.send(embed=embed, file=discord_file)

            # --- 情況二：抽到 Discord 圖片 ---
            else:
                image_url = pick['url']
                image_name = pick['name']
                
                # 建立 Embed
                embed = discord.Embed(
                    title="🖼️ 隨機圖庫圖片",
                    description="這是從雲端圖庫中隨機挑選的精彩照片！",
                    color=discord.Color.blue()
                )
                # 雲端圖片直接用 URL
                embed.set_image(url=image_url)
                embed.set_footer(text=f"來源: 頻道歷史 | 檔名: {image_name}")
                
                # 發送 (絕對不要傳 file 參數)
                await interaction.followup.send(embed=embed)

        except Exception as e:
            print(f"❌ 發送圖片時發生嚴重錯誤: {e}")
            await interaction.followup.send(f"❌ 發生未預期的錯誤: {e}", ephemeral=True)


class ScheduledUploadCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 🚨 啟動定時任務
        self.upload_scheduler.start() 
        print("✅ 排程上傳任務已啟動。")

    def cog_unload(self):
        self.upload_scheduler.cancel() 
        
    async def upload_and_clear_local_files(self):
        """掃描本地暫存資料夾，上傳到 Discord 並清除。"""
        
        # 使用 try-except 確保 Bot 找不到頻道時不會崩潰
        try:
            channel = self.bot.get_channel(int(TARGET_CHANNEL_ID))
        except ValueError:
            print(f"❌ 錯誤：TARGET_CHANNEL_ID 必須是有效的數字 ID: {TARGET_CHANNEL_ID}")
            return

        if not channel:
            print(f"❌ 錯誤：找不到目標頻道 ID: {TARGET_CHANNEL_ID}")
            return
            
        # 1. 搜尋所有待上傳的圖片檔案
        search_path = os.path.join(TEMP_UPLOAD_FOLDER, '*')
        files_to_upload = glob.glob(search_path)
        
        if not files_to_upload:
            return

        print(f"📦 發現 {len(files_to_upload)} 個檔案待上傳...")
        
        uploaded_count = 0
        
        for file_path in files_to_upload:
            try:
                # 2. 上傳到 Discord
                discord_file = discord.File(file_path, filename=os.path.basename(file_path))
                await channel.send(
                    file=discord_file
                )
                
                # 3. 成功後刪除本地檔案
                os.remove(file_path)
                uploaded_count += 1
                
            except Exception as e:
                print(f"❌ 上傳/刪除檔案 {file_path} 時發生錯誤: {e}")
                # 繼續處理下一個檔案

        print(f"✅ 排程任務完成，成功上傳 {uploaded_count} 個檔案。")


    # 🚨 每10分鐘執行一次
    @tasks.loop(minutes=10)
    async def upload_scheduler(self):
        """定時執行上傳任務"""
        await self.bot.wait_until_ready() 
        print(f"\n--- 執行排程上傳任務 @ {safe_now()} ---")
        await self.upload_and_clear_local_files()
        print("--- 排程任務結束 ---\n")

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
    async def gay_probability(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        # 紀錄指令使用
        await log_command(interaction, "/gay")
        
        target_user = user if user else interaction.user
        
        # 1. 計算機率
        if target_user.id in HUNDRED_PERCENT_IDS:
            probability = 100
        elif target_user.id in SPECIAL_USER_IDS:
            probability = 0
        elif target_user.id in SIXTY_NINE_IDS:
            probability = 69
        else:
            probability = random.randint(1, 100)
            
        # 2. 準備顯示文字
        display_text = f"**{probability}%**"
        
        # 如果是 69，直接加上 ♋️
        if probability == 69:
            display_text += " ♋️"
            
        # 3. 建立並發送 Embed
        embed = discord.Embed(
            title="🏳️‍🌈 隨機同性戀機率 (/gay)", 
            color=discord.Color.random()
        )
        embed.add_field(name="測試者", value=target_user.mention, inline=False)
        embed.add_field(name="機率為", value=display_text, inline=False)
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

FLAG_EMOJI = "🚩"

class MinesweeperTextView(discord.ui.View):
    def __init__(self, cog, rows: int, cols: int, mines: int, difficulty_name: str):
        super().__init__(timeout=600)
        self.cog = cog
        
        self.T_ROWS = rows
        self.T_COLS = cols
        self.T_MINES = mines
        self.difficulty_name = difficulty_name  # 儲存難度名稱
        
        self.player_id: Optional[int] = None
        self.board: List[List[str]] = []
        self.covered_board: List[List[bool]] = []
        self.flagged: List[List[bool]] = []
        self.cursor = [0, 0]
        self.game_over = False
        self.message: Optional[discord.Message] = None
        self.initialize_board()
        self.setup_buttons()

    def initialize_board(self):
        """初始化地雷位置和數字"""
        self.board = [["" for _ in range(self.T_COLS)] for _ in range(self.T_ROWS)]
        self.covered_board = [[True for _ in range(self.T_COLS)] for _ in range(self.T_ROWS)]
        self.flagged = [[False for _ in range(self.T_COLS)] for _ in range(self.T_ROWS)]
        
        mine_positions = random.sample(range(self.T_ROWS * self.T_COLS), self.T_MINES)
        for idx in mine_positions:
            r, c = divmod(idx, self.T_COLS)
            self.board[r][c] = "💥"

        for r in range(self.T_ROWS):
            for c in range(self.T_COLS):
                if self.board[r][c] == "💥": continue
                mine_count = 0
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        if dr == 0 and dc == 0: continue
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < self.T_ROWS and 0 <= nc < self.T_COLS and self.board[nr][nc] == "💥":
                            mine_count += 1
                self.board[r][c] = str(mine_count) if mine_count > 0 else "0"

    def render_board(self):
        """將二維陣列渲染成文字遊戲板，確保排版對齊"""
        output = []
        # 頂部顯示座標提示
        col_header = " ".join([str(i % 10) for i in range(self.T_COLS)])
        output.append("   " + col_header) 
        
        for r in range(self.T_ROWS):
            # 左側顯示座標
            row_str = f"{r % 10} " 
            for c in range(self.T_COLS):
                char = ""
                
                # 判斷當前格子的內容
                if r == self.cursor[0] and c == self.cursor[1] and not self.game_over:
                    char = "⭕" 
                elif self.game_over:
                    content = self.board[r][c]
                    if content == "💥": char = "💥"
                    elif self.flagged[r][c] and content != "💥": char = "❌"
                    elif content == "0": char = "⬜" 
                    else: char = content
                elif self.flagged[r][c]:
                    char = FLAG_EMOJI
                elif self.covered_board[r][c]:
                    char = "❓" 
                else:
                    content = self.board[r][c]
                    if content == "0": char = "⬜" 
                    else: char = content
                
                # 核心修正：統一寬度
                if char in "012345678":
                    # 單個數字 (1 寬度) + 1 個空格 = 2 寬度
                    row_str += f"{char} " 
                else:
                    # 表情符號 (2 寬度) = 2 寬度
                    row_str += char 
                    
            output.append(row_str)
        
        return "\n".join(output)

    def setup_buttons(self):
        """設置移動和操作按鈕，包含重新開始按鈕"""
        self.clear_items()
        
        # Row 0: ⬅️ ⬆️ ➡️
        self.add_item(self.create_move_button("⬅️", -1, 0, discord.ButtonStyle.secondary, 0)) # 左
        self.add_item(self.create_move_button("⬆️", 0, -1, discord.ButtonStyle.secondary, 0)) # 上
        self.add_item(self.create_move_button("➡️", 1, 0, discord.ButtonStyle.secondary, 0)) # 右

        # Row 1: ⬇️ 和 重新開始按鈕
        self.add_item(self.create_move_button("⬇️", 0, 1, discord.ButtonStyle.secondary, 1)) # 下
        
        restart_button = discord.ui.Button(
            label="🔄 重新開始",
            style=discord.ButtonStyle.blurple,
            custom_id="action_restart",
            row=1
        )
        restart_button.callback = self.restart_callback
        self.add_item(restart_button)

        # Row 2: 操作按鈕
        open_button = discord.ui.Button(
            label="✅ 就這一個！", 
            style=discord.ButtonStyle.success, 
            custom_id="action_open", 
            row=2,
            disabled=self.game_over
        )
        open_button.callback = self.action_callback
        self.add_item(open_button)

        flag_button = discord.ui.Button(
            label="🚩 插旗", 
            style=discord.ButtonStyle.danger, 
            custom_id="action_flag", 
            row=2,
            disabled=self.game_over
        )
        flag_button.callback = self.action_callback
        self.add_item(flag_button)

    def create_move_button(self, label, dc, dr, style, row):
        """創建移動按鈕"""
        button = discord.ui.Button(
            label=label, 
            style=style, 
            custom_id=f"move_{dc}_{dr}",
            disabled=self.game_over,
            row=row
        )
        button.callback = self.move_callback
        return button

    async def move_callback(self, interaction: Interaction):
        """處理移動按鈕點擊"""
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message("這不是您的遊戲。", ephemeral=True)
        
        if self.game_over:
            return await interaction.response.edit_message(content=f"遊戲已結束。\n```\n{self.render_board()}\n```", view=self)

        await interaction.response.defer()
        
        _, dc_str, dr_str = interaction.data['custom_id'].split('_')
        dc, dr = int(dc_str), int(dr_str)
        
        new_c = max(0, min(self.T_COLS - 1, self.cursor[1] + dc))
        new_r = max(0, min(self.T_ROWS - 1, self.cursor[0] + dr))
        
        self.cursor = [new_r, new_c]
        
        await interaction.edit_original_response(
            content=f"挑戰者：<@{self.player_id}>\n請移動光標 (⭕) 並選擇操作。\n\n```\n{self.render_board()}\n```", 
            view=self
        )

    async def action_callback(self, interaction: Interaction):
        """處理操作按鈕點擊 (翻開/插旗)"""
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message("這不是您的遊戲。", ephemeral=True)
        
        if self.game_over:
            return await interaction.response.edit_message(content=f"遊戲已結束。\n```\n{self.render_board()}\n```", view=self)

        await interaction.response.defer()
        
        r, c = self.cursor[0], self.cursor[1]
        action = interaction.data['custom_id'].split('_')[1]
        
        content_update = f"請移動光標 (⭕) 並選擇操作。"

        if not self.covered_board[r][c] and action == 'open':
            pass
        elif action == 'flag':
            self.flagged[r][c] = not self.flagged[r][c]
        elif action == 'open' and self.flagged[r][c]:
            await interaction.followup.send("請先移除旗子再翻開。", ephemeral=True)
            return
        elif action == 'open' and self.board[r][c] == "💥":
            await self.end_game(interaction, False)
            return
        elif action == 'open':
            self.reveal_tile(r, c)
            if self.check_win():
                await self.end_game(interaction, True)
                return
        
        await interaction.edit_original_response(
            content=f"挑戰者：<@{self.player_id}>\n{content_update}\n\n```\n{self.render_board()}\n```", 
            view=self
        )

    async def restart_callback(self, interaction: Interaction):
        """重新開始遊戲"""
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message("這不是您的遊戲。", ephemeral=True)
        
        self.stop() 
        if self.player_id in self.cog.active_games:
            del self.cog.active_games[self.player_id]

        await interaction.response.defer()

        # 呼叫 Cog 中的邏輯來重新啟動遊戲
        await self.cog.start_new_game(interaction, self.difficulty_name)

    def reveal_tile(self, r: int, c: int):
        """遞歸翻開格子，如果為 0 則翻開周圍"""
        if not (0 <= r < self.T_ROWS and 0 <= c < self.T_COLS) or not self.covered_board[r][c] or self.flagged[r][c]:
            return

        self.covered_board[r][c] = False
        
        if self.board[r][c] == "0":
            for dr in [-1, 0, 1]:
                for dc in [-1, 0, 1]:
                    if dr == 0 and dc == 0:
                        continue
                    self.reveal_tile(r + dr, c + dc)

    def check_win(self):
        """檢查是否勝利 (所有非地雷格子都已翻開)"""
        total_safe_tiles = self.T_ROWS * self.T_COLS - self.T_MINES
        uncovered_count = sum(1 for r in range(self.T_ROWS) for c in range(self.T_COLS) 
                              if not self.covered_board[r][c] and self.board[r][c] != "💥")
        return uncovered_count == total_safe_tiles

    async def end_game(self, interaction: Interaction, is_win: bool):
        """結束遊戲並更新訊息"""
        self.game_over = True
        self.setup_buttons() # 禁用按鈕
        
        content = f"恭喜 <@{self.player_id}>！你成功通過了地雷區👍" if is_win else f"很遺憾 <@{self.player_id}>！你爆炸了💥！"
        
        if self.player_id in self.cog.active_games:
            del self.cog.active_games[self.player_id]

        final_content = f"**{content}**\n\n```\n{self.render_board()}\n```"
        
        await interaction.edit_original_response(content=final_content, view=None) 
        self.stop()
        
    async def on_timeout(self):
        """處理遊戲超時"""
        if not self.game_over and self.message:
            self.game_over = True
            self.setup_buttons()
            
            final_content = f"**遊戲超時了，地雷區挑戰失敗。**\n\n```\n{self.render_board()}\n```"
            await self.message.edit(content=final_content, view=None)
                
        if self.player_id in self.cog.active_games:
            del self.cog.active_games[self.player_id]
            
class MinesweeperTextCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_games: Dict[int, MinesweeperTextView] = {} 

    async def start_new_game(self, interaction: Interaction, difficulty: str):
        """處理遊戲初始化和訊息發送/編輯的核心邏輯"""
        player_id = interaction.user.id
        
        # 1. 根據難度設定參數
        if difficulty == "超簡單":
            rows, cols, mines = 5, 5, 3
        elif difficulty == "簡單":
            rows, cols, mines = 7, 7, 8
        elif difficulty == "困難":
            rows, cols, mines = 12, 12, 30
        else: # 一般
            rows, cols, mines = 10, 10, 15
            difficulty = "一般" # 確保名稱是正確的

        # 2. 遊戲初始化 (傳入難度名稱)
        view = MinesweeperTextView(self, rows, cols, mines, difficulty)
        view.player_id = player_id 
        self.active_games[player_id] = view
        
        # 3. 準備訊息內容
        initial_content = (
            f"挑戰者：{interaction.user.mention} (難度：{difficulty} {rows}x{cols}, {mines}雷)\n"
            f"目標：找到全部{rows * cols - mines}個安全格子，不要踩到 {mines} 顆炸彈！"
        )
        message_content = f"{initial_content}\n\n```\n{view.render_board()}\n```"
        
        # 4. 發送/編輯訊息
        try:
            # 嘗試使用 send_message 進行初始響應 (通常用於 /指令)
            await interaction.response.send_message(
                content=message_content,
                view=view
            )
            view.message = await interaction.original_response()
        except discord.errors.InteractionResponded:
            # 如果已經響應 (用於重新開始按鈕的回調)，則編輯原訊息
            await interaction.edit_original_response(
                content=message_content,
                view=view
            )
            view.message = await interaction.original_response()

    @app_commands.command(name="踩地雷", description="開始一個文字版踩地雷遊戲！")
    @app_commands.describe(difficulty="選擇遊戲難度")
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="超簡單 (5x5, 3雷)", value="超簡單"),
        app_commands.Choice(name="簡單 (7x7, 8雷)", value="簡單"),
        app_commands.Choice(name="一般 (10x10, 15雷)", value="一般"),
        app_commands.Choice(name="困難 (12x12, 30雷)", value="困難"),
    ])
    async def minesweeper_text_game(self, interaction: Interaction, difficulty: Literal["超簡單", "簡單", "一般", "困難"] = "一般"):
        player_id = interaction.user.id
        
        # 檢查是否已有活躍遊戲
        if player_id in self.active_games:
            current_game = self.active_games[player_id]
            if not current_game.game_over and not current_game.is_finished():
                await interaction.response.send_message("❌ **無法同時開啟兩次踩地雷！** 您目前正在進行一個遊戲。", ephemeral=True)
                return
            else:
                # 清理舊的已結束遊戲
                del self.active_games[player_id] 
        
        # 呼叫核心邏輯開始遊戲
        await self.start_new_game(interaction, difficulty)



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
    async def interaction_check(self, interaction: Interaction) -> bool:
        vc = self.cog.vc_dict.get(self.guild_id)
        
        if not vc or not vc.is_connected():
            return interaction.user.guild_permissions.administrator
        
        if interaction.user.voice and interaction.user.voice.channel == vc.channel:
            return True
            
        return interaction.user.guild_permissions.administrator
        
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
    # 確保 on_ready 邏輯只運行一次（特別是針對載入 Cogs）
    if getattr(bot, "_has_ready_run", False):
        # 如果已經運行過，只同步指令和設定狀態，避免重複載入 Cog
        # ⚠️ 注意：這裡省略了 return，讓指令同步和狀態設定能再次運行
        pass 
    else:
        # 第一次運行
        bot._has_ready_run = True

        global discord_loop
        try:
            discord_loop = asyncio.get_running_loop()
        except Exception:
            discord_loop = None

        print(f"[{safe_now()}] Bot logged in as {bot.user} ({bot.user.id})")

        # --- 1. 載入 Cogs (僅在 Bot 啟動時執行一次) ---
        try:
            # 這是您原來的 Cog 列表
            await bot.add_cog(HelpCog(bot))
            await bot.add_cog(LogsCog(bot))
            await bot.add_cog(PingCog(bot))
            await bot.add_cog(ReactionRoleCog(bot))
            await bot.add_cog(UtilityCog(bot))
            await bot.add_cog(MinesweeperTextCog(bot))
            await bot.add_cog(ModerationCog(bot))
            await bot.add_cog(FunCog(bot))
            await bot.add_cog(SupportCog(bot))
            await bot.add_cog(VoiceCog(bot))
            await bot.add_cog(ImageDrawCog(bot))
            await bot.add_cog(ScheduledUploadCog(bot)) 
            await bot.add_cog(BackupSystem(bot)) 
            print("✅ All Cogs loaded.")
        except Exception as e:
            print("❌ Cog add error:", e)
            traceback.print_exc()

        # --- 2. 註冊持久化 View ---
        try:
            bot.add_view(ReplyView())

            bot.add_view(RestorePreCheckView(None, None, None))
            
            print("✅ ReplyView & BackupViews registered.")
        except Exception as e:
            print(f"持久化設定失敗: {e}")

        # --- 2. 註冊持久化 View ---
        try:
            bot.add_view(ReplyView())
            print("✅ ReplyView persistent class registered.")
        except Exception as e:
            print("持久話伺服器設定設定失敗: {e}")

    # --- 3. 同步斜線指令 (每次 on_ready 都應該運行) ---
    try:
        await bot.tree.sync() 
        print("✅ 斜線指令已同步完成。")
    except Exception as e:
        print(f"❌ 同步指令時發生錯誤: {e}")

    # --- 4. 設定 Bot 狀態 (每次 on_ready 都應該運行) ---
    try:
        await bot.change_presence(status=discord.Status.online, activity=discord.Game(name="服務中 | /help"))
    except Exception:
        pass


import os
import asyncio
import requests
import discord
from flask import Flask, render_template, session, redirect, url_for, request, jsonify, flash, send_from_directory
from werkzeug.utils import secure_filename
import uuid # 用於生成獨特的檔案名
import random # 用於隨機邏輯 (儘管在此版本中已棄用)
# 🚨 確保您已在專案中定義這些工具
from utils import load_config, save_config, safe_now 


app = Flask(__name__)
# 建議使用環境變數設定 FLASK_SECRET_KEY
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change_this_to_secure_key")


# ===============================================
# 🔧 基礎配置與變數
# ===============================================

# 1. 獲取當前程式碼所在的「絕對路徑」
basedir = os.path.abspath(os.path.dirname(__file__))

# 允許的圖片擴展名
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Discord OAuth2 設定 (使用您的環境變數)
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DISCORD_API_BASE_URL = "https://discord.com/api/v10"
TOKEN_URL = f"{DISCORD_API_BASE_URL}/oauth2/token"
USER_URL = f"{DISCORD_API_BASE_URL}/users/@me"

# 權限設定 (與您的儀表板邏輯保持一致)
ADMINISTRATOR_PERMISSION = 0x8
SPECIAL_USER_IDS = [1238436456041676853] 
LOG_VIEWER_IDS = [1238436456041676853]    
# --------------------------


# ===============================================
# 🖼️ 圖片上傳與抽圖服務 (統一區塊)
# ===============================================

# 🚨 配置區塊 - 請務必替換 TARGET_CHANNEL_ID
# 暫存區：用於存放網頁上傳後、Bot 尚未轉發前的圖片
TEMP_UPLOAD_FOLDER = 'static/temp_uploads' 

# 💡 內部通訊 URL：Bot 服務的 HTTP 代理端口 (Render 服務間通常為 localhost:8080)
BOT_API_URL = "http://localhost:8080/api/upload_proxy" 

# 🚨 替換成您希望圖片發送到的 Discord 頻道 ID
TARGET_CHANNEL_ID = "1446781237422198855" 

# 設定 Flask 檔案儲存路徑為暫存區
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, TEMP_UPLOAD_FOLDER) 

# 建立暫存資料夾
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
    print(f"✅ 已建立暫存資料夾: {app.config['UPLOAD_FOLDER']}")


def allowed_file(filename):
    """檢查檔案副檔名是否在允許列表中。"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- 路由: 網頁上傳處理 (完整替換) ---
@app.route('/upload_web', methods=['GET', 'POST'])
def upload_file_from_web():
    if request.method == 'POST':
        # 1. 檢查是否有檔案上傳
        if 'file' not in request.files:
            # 如果使用者提交表單但沒有選擇檔案
            return render_template('upload.html', message="❌ 請選擇檔案", status="error")
        
        # 2. 使用 getlist('input_name') 獲取所有檔案 (支援多檔案)
        uploaded_files = request.files.getlist('file')
        
        saved_count = 0
        error_count = 0
        
        for file in uploaded_files:
            # 檢查檔案是否有效且檔名不為空
            if file.filename == '':
                continue
                
            if file and allowed_file(file.filename):
                try:
                    extension = file.filename.rsplit('.', 1)[1].lower()
                    # 使用 UUID 作為檔名，確保唯一性
                    random_filename = f"{uuid.uuid4().hex}.{extension}"
                    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], random_filename)
                    
                    # 儲存到本地暫存資料夾
                    file.save(temp_path)
                    saved_count += 1
                    
                except Exception as e:
                    # 儲存時發生錯誤
                    print(f"儲存檔案時發生錯誤: {e}")
                    error_count += 1
            else:
                # 檔案格式不支援
                error_count += 1

        # 3. 根據結果返回訊息
        if saved_count > 0:
            # 成功儲存至少一個檔案
            message = f"✅ 成功上傳 {saved_count} 張圖片！圖片已暫存，將於下一排程時間由 Bot 轉發至 Discord。"
            status = "success"
        elif error_count > 0 and saved_count == 0:
            # 雖然選擇了檔案，但所有檔案都不符合要求
            message = "❌ 所有選擇的檔案都無效或格式不支援 (僅限 png, jpg, jpeg, gif)。"
            status = "error"
        else:
            # 理論上不應該發生，但作為 fallback
            message = "❌ 請選擇檔案"
            status = "error"
            
        return render_template('upload.html', message=message, status=status)

    # GET 請求：顯示上傳頁面
    return render_template('upload.html')


# --- 路由: 棄用舊的圖片服務 API (替換您的 /random_image 函式) ---
@app.route('/random_image', methods=['GET'])
def get_random_image_deprecated():
    return jsonify({'success': False, 'message': '圖片服務已改為定時排程上傳，請使用 Bot 的 /抽圖 指令。'}), 404


# ===============================================
# 🔐 Discord OAuth2 儀表板 (您的原邏輯)
# ===============================================

# OAuth2 登入頁面
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
    # 🚨 注意: 這裡的 bot.get_guild 依賴於您的 Bot 程式碼
    # filtered_guilds = [g for g in admin_guilds if bot.get_guild(int(g['id']))] 
    filtered_guilds = admin_guilds # 暫時使用所有管理伺服器，避免未定義錯誤

    return render_template(
        'dashboard.html',
        user=user_data,
        guilds=filtered_guilds,
        is_special_user=is_special_user,
        DISCORD_CLIENT_ID=DISCORD_CLIENT_ID
    )

# --------------------------
# 伺服器儀表板/設定 (其餘路由保持不變)
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
        return "❌ 內部錯誤：Discord 機器人事件循環尚未啟動。", 503

    # 由於 settings 會執行更嚴格的檢查，這裡保持 redirect
    # if bot.get_guild(guild_id) is None: pass 

    return redirect(url_for('settings', guild_id=guild_id))

# 伺服器設定
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
        return "❌ 內部錯誤：Discord 機器人事件循環尚未啟動。", 503

    # ❗ 修正點 1.1: 嘗試從緩存獲取
    guild_obj = bot.get_guild(guild_id)
    
    # ❗ 修正點 1.2: 如果緩存失敗，嘗試異步 API 獲取
    if guild_obj is None:
        try:
            future_guild = asyncio.run_coroutine_threadsafe(bot.fetch_guild(guild_id), discord_loop)
            # 等待 5 秒鐘 API 響應
            guild_obj = future_guild.result(timeout=5)
        except Exception as e:
            # API 獲取失敗 (例如機器人不在伺服器或超時)
            guild_obj = None 
            print(f"API Fetch Guild Error: {e}")

    if guild_obj is None:
        return "❌ 錯誤：找不到該伺服器、機器人不在其中，或連線超時。", 404

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

# 成員列表
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
        return "❌ 內部錯誤：Discord 機器人事件循環尚未啟動。", 503

    try:
        # ❗ 修正點 2.1: 嘗試從緩存獲取
        guild_obj = bot.get_guild(guild_id)
        if guild_obj is None:
            # 如果緩存失敗，嘗試異步 API 獲取
            future_guild = asyncio.run_coroutine_threadsafe(bot.fetch_guild(guild_id), discord_loop)
            guild_obj = future_guild.result(timeout=5)
            
        if not guild_obj:
            return "❌ 找不到這個伺服器", 404

        # 這裡獲取成員需要異步，保留 run_coroutine_threadsafe
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

# 通知模態
@app.route("/guild/<int:guild_id>/settings/notifications_modal", methods=['GET'])
def notifications_modal(guild_id):
    global discord_loop
    if discord_loop is None or not discord_loop.is_running():
        return "❌ 載入設定失敗！錯誤：Discord 機器人事件循環尚未啟動。", 503

    try:
        # 3.1 嘗試從緩存獲取
        guild_obj = bot.get_guild(guild_id)

        # 3.2 如果緩存失敗，異步 API 獲取
        if guild_obj is None:
            # 使用 fetch_guild
            future_guild = asyncio.run_coroutine_threadsafe(bot.fetch_guild(guild_id), discord_loop)
            # 等待結果
            guild_obj = future_guild.result(timeout=5)
            
        if guild_obj is None:
            return f"❌ 找不到伺服器 ID **{guild_id}**。機器人可能已離開或 ID 無效。", 404
            
        # ... (從緩存讀取並處理配置的邏輯) ...
        channels = guild_obj.text_channels
        config = load_config(guild_id)
        
        video_channel_id = str(config.get('video_notification_channel_id', ''))
        video_message = config.get('video_notification_message', 'New Video from {channel}: {title}\n{link}')
        live_message = config.get('live_notification_message', '@everyone {channel} is Live! {title}\n{link}')
        ping_role = config.get('ping_role', '')
        content_filter = config.get('content_filter', 'Videos,Livestreams')

        data = {
            'guild_obj': guild_obj,
            'channels': channels,
            'video_channel_id': video_channel_id,
            'video_message': video_message,
            'live_message': live_message,
            'ping_role': ping_role,
            'content_filter': content_filter
        }
        
        return render_template('modal_notifications.html', **data)

    # ❗ 修正點：捕獲所有 Discord API 異常
    except discord.HTTPException as e:
        # 捕獲所有 API 錯誤 (例如 404, 403)
        return f"❌ 載入設定失敗！錯誤：Discord API 報告錯誤 ({e.status})。訊息: {e.text}", e.status
    except TimeoutError:
        # 連線超時錯誤
        return f"❌ 載入設定失敗！錯誤：與 Discord API 連線超時（>5 秒）。", 500
    except Exception as e:
        # 捕獲所有其他非預期錯誤
        return f"❌ 載入設定失敗！錯誤：在處理資料時發生意外錯誤。訊息: {e}", 500

# 日誌
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

    return render_template('all_logs.html', logs=COMMAND_LOGS)

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

    # 🚨 這裡需要確保 COMMAND_LOGS 是在全局範圍內可訪問的
    return jsonify(COMMAND_LOGS) 


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
def run_web():
    """在單獨的執行緒中運行 Flask 應用程式。"""
    
    # 🚨 Render 預設提供 $PORT (約 10000)。
    # 確保備用端口與 Bot API 端口 8080 衝突。
    port = os.getenv("PORT", 8081)  # 使用 8081 作為備用端口
    
    print(f"Flask Web 正在嘗試啟動於端口: {port}")
    
    # 使用 Flask 內建的開發伺服器啟動 (生產環境建議使用 Waitress)
    try:
        # 如果您使用 Waitress: serve(app, host="0.0.0.0", port=int(port))
        app.run(host="0.0.0.0", port=int(port), debug=False, use_reloader=False)
    except Exception as e:
        print(f"❌ Flask Web 啟動失敗: {e}")

def keep_web_alive():
    """在背景執行緒中啟動 Flask 服務。"""
    t = threading.Thread(target=run_web)
    t.daemon = True # 設置為守護線程，當主程序退出時它也會退出
    t.start()
    print("Flask Web 已啟動於背景線程。")



async def start_bot():
    """啟動 Discord Bot 的 asyncio 主循環。"""
    global discord_loop
    discord_loop = asyncio.get_running_loop()
    print("啟動 Discord Bot...")
    try:
        # 這是 Discord.py 啟動的主循環
        await bot.start(TOKEN) 
    except KeyboardInterrupt:
        print("機器人已手動關閉。")
    except Exception as e:
        print(f"Discord Bot 啟動錯誤: {e}")
        traceback.print_exc()
        
if __name__ == "__main__":
    # 1️⃣ 在背景執行緒中啟動 Flask Web 服務 (綁定 10000)
    keep_web_alive()

    # 2️⃣ 在主線程中啟動 Discord Bot (Bot 的 on_ready 會啟動 8080 服務)
    try:
        # asyncio.run 會運行 start_bot，直到它完成（Bot 運行時不會完成）
        asyncio.run(start_bot())
    except RuntimeError as e:
        # 處理常見的 Event Loop 關閉錯誤
        if "Event loop is closed" in str(e) or "cannot run from a thread" in str(e):
            print("⚠️ Event loop 已關閉或不可從當前線程啟動。")
        else:
            raise