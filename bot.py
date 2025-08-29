import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import os
from datetime import timedelta

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
OWNER_ID = 1238436456041676853

#-----------------------------
#防多實例重複執行設定
#-----------------------------
MAIN_BOT_ID = int(os.environ.get("MAIN_BOT_ID", 0))
def is_main_instance():
    return bot.user.id == MAIN_BOT_ID or MAIN_BOT_ID == 0



import os
from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is running!")

app = web.Application()
app.add_routes([web.get("/", handle)])

port = int(os.environ.get("PORT", 8080))
web.run_app(app, host="0.0.0.0", port=port)

#-----------------------------
#全域變數：抽獎狀態
#-----------------------------
active_giveaways = {}

#-----------------------------
#/say
#-----------------------------
from discord import app_commands
from discord.ext import commands
import discord

SPECIAL_USER_IDS = [OWNER_ID]

class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="say", description="讓機器人發送訊息")
    async def say(
        self, 
        interaction: discord.Interaction, 
        message: str, 
        channel_name: str = None, 
        user_id: str = None
    ):
        # 權限檢查
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            return

        # 發送給指定使用者
        if user_id:
            try:
                user = await self.bot.fetch_user(int(user_id))
                await user.send(message)
                await interaction.response.send_message(f"✅ 已發送私訊給 {user.name}", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ 發送失敗: {e}", ephemeral=True)
            return

        # 發送到指定頻道
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            await interaction.response.send_message(f"❌ 找不到頻道 `{channel_name}`", ephemeral=True)
            return

        await channel.send(message)
        await interaction.response.send_message(f"✅ 已在 {channel.mention} 發送訊息", ephemeral=True)

#-----------------------------
#calc
#-----------------------------
@tree.command(name="calc", description="簡單計算器")
@app_commands.describe(expr="例如：1+2*3")
async def calc(interaction: discord.Interaction, expr: str):
    try:
        allowed = "0123456789+-*/(). "
        if not all(c in allowed for c in expr):
            raise ValueError("包含非法字符")
        result = eval(expr)
        await interaction.response.send_message(f"結果：{result}")
    except Exception as e:
        await interaction.response.send_message(f"計算錯誤：{e}")


from discord.ext import commands
from discord import app_commands
import discord
import random

class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="draw", description="隨機抽選一個選項")
    @app_commands.describe(options="輸入多個選項，用逗號或空格分隔")
    async def draw(self, interaction: discord.Interaction, options: str):
        # 將使用者輸入拆分成列表
        if "," in options:
            items = [o.strip() for o in options.split(",") if o.strip()]
        else:
            items = [o.strip() for o in options.split() if o.strip()]

        if len(items) < 2:
            await interaction.response.send_message("❌ 請至少輸入兩個選項", ephemeral=True)
            return

        winner = random.choice(items)
        await interaction.response.send_message(f"🎉 抽選結果：**{winner}**")
#-----------------------------
# /announce
#-----------------------------
@tree.command(name="announce", description="發布公告（管理員限定）")
@app_commands.describe(
    title="公告標題",
    content="公告內容",
    channel="公告頻道（可不選）",
    ping_everyone="是否要 @everyone"
)
async def announce(interaction: discord.Interaction, title: str, content: str, channel: discord.TextChannel = None, ping_everyone: bool = False):
    if not is_main_instance():
        await interaction.response.send_message("❌ 目前這個 Bot instance 不負責發送公告", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 只有管理員能發布公告", ephemeral=True)
        return
    target_channel = channel or interaction.channel
    embed = discord.Embed(title=f"📢 {title}", description=content, color=discord.Color.orange())
    embed.set_footer(text=f"發布者：{interaction.user.display_name}")
    await interaction.response.send_message(f"✅ 公告已發佈到 {target_channel.mention}！", ephemeral=True)
    mention = "@everyone" if ping_everyone else ""
    await target_channel.send(mention, embed=embed)

from discord.ext import commands
from discord import app_commands
import discord
import random
import asyncio
import re

class DrawCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_draws = {}  # key: guild_id, value: dict {name, max_winners, participants, task}

    # 解析時間字串，支援 10s / 5m / 1h
    def parse_duration(self, timestr: str) -> int:
        pattern = r"(\d+)([smh])"
        match = re.fullmatch(pattern, timestr.strip().lower())
        if not match:
            raise ValueError("時間格式錯誤，範例: 10s, 5m, 1h")
        number, unit = match.groups()
        number = int(number)
        if unit == "s":
            return number
        elif unit == "m":
            return number * 60
        elif unit == "h":
            return number * 3600
        else:
            raise ValueError("不支援的時間單位")

    @app_commands.command(name="start_draw", description="開始一場抽獎")
    async def start_draw(self, interaction: discord.Interaction, name: str, max_winners: int = 1, duration: str = "60s"):
        """
        duration: 抽獎持續時間，格式: 10s / 5m / 1h
        """
        guild_id = interaction.guild.id
        if guild_id in self.active_draws:
            await interaction.response.send_message("❌ 本伺服器已有正在進行的抽獎", ephemeral=True)
            return
        
        try:
            seconds = self.parse_duration(duration)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        draw_info = {
            "name": name,
            "max_winners": max_winners,
            "participants": set(),
            "task": None
        }
        self.active_draws[guild_id] = draw_info

        # 建立定時任務，自動結束抽獎
        draw_info["task"] = asyncio.create_task(self._auto_end_draw(interaction, guild_id, seconds))

        await interaction.response.send_message(
            f"🎉 抽獎 `{name}` 已開始！使用 /join_draw 參加。名額: {max_winners}。\n⏱ 持續 {duration} 後自動結束。"
        )

    @app_commands.command(name="join_draw", description="參加抽獎")
    async def join_draw(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if guild_id not in self.active_draws:
            await interaction.response.send_message("❌ 沒有正在進行的抽獎", ephemeral=True)
            return

        draw = self.active_draws[guild_id]
        draw["participants"].add(interaction.user.id)
        await interaction.response.send_message(f"✅ {interaction.user.mention} 已加入 `{draw['name']}` 抽獎！", ephemeral=True)

    async def _auto_end_draw(self, interaction, guild_id, duration_seconds):
        await asyncio.sleep(duration_seconds)
        if guild_id not in self.active_draws:
            return

        draw = self.active_draws.pop(guild_id)
        participants = list(draw["participants"])

        if not participants:
            await interaction.channel.send(f"❌ 抽獎 `{draw['name']}` 沒有人參加。")
            return

        winners = random.sample(participants, min(draw["max_winners"], len(participants)))
        winners_mentions = [f"<@{uid}>" for uid in winners]

        await interaction.channel.send(f"🏆 抽獎 `{draw['name']}` 結束！得獎者：{', '.join(winners_mentions)}")

#載入
async def setup():
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(FunCog(bot))
    await bot.add_cog(DrawCog(bot))

asyncio.run(setup())

#-----------------------------
#Bot 啟動
#-----------------------------
@bot.event
async def on_ready():
    print(f"✅ Bot 已啟動！登入身分：{bot.user}")
    await tree.sync()
import os

TOKEN = os.getenv("DISCORD_TOKEN")  # 建議放在環境變數
# 或直接
# TOKEN = "你的真正 BOT TOKEN"

bot.run(TOKEN)