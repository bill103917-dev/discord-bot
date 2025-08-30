import os
import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import re
from aiohttp import web

# =========================
# ⚡ 基本設定
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = 1238436456041676853
SPECIAL_USER_IDS = [OWNER_ID]

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# 防多實例
# -------------------------
MAIN_BOT_ID = int(os.environ.get("MAIN_BOT_ID", 0))
def is_main_instance():
    return bot.user.id == MAIN_BOT_ID or MAIN_BOT_ID == 0

# =========================
# ⚡ Cog: 工具指令
# =========================
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="say", description="讓機器人發送訊息")
    async def say(self, interaction: discord.Interaction, message: str, channel_name: str = None, user_id: str = None):
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            return

        if user_id:
            try:
                user = await self.bot.fetch_user(int(user_id))
                await user.send(message)
                await interaction.response.send_message(f"✅ 已發送私訊給 {user.name}", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ 發送失敗: {e}", ephemeral=True)
            return

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            await interaction.response.send_message(f"❌ 找不到頻道 `{channel_name}`", ephemeral=True)
            return

        await channel.send(message)
        await interaction.response.send_message(f"✅ 已在 {channel.mention} 發送訊息", ephemeral=True)

    @app_commands.command(name="calc", description="簡單計算器")
    @app_commands.describe(expr="例如：1+2*3")
    async def calc(self, interaction: discord.Interaction, expr: str):
        try:
            allowed = "0123456789+-*/(). "
            if not all(c in allowed for c in expr):
                raise ValueError("包含非法字符")
            result = eval(expr)
            await interaction.response.send_message(f"結果：{result}")
        except Exception as e:
            await interaction.response.send_message(f"計算錯誤：{e}")

# =========================
# ⚡ Cog: 遊戲指令
# =========================
class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rps_choices = {"剪刀":"✂️", "石頭":"🪨", "布":"📄"}

    @app_commands.command(name="rps", description="剪刀石頭布")
    async def rps(self, interaction: discord.Interaction, choice: str):
        if choice not in self.rps_choices:
            await interaction.response.send_message("❌ 請輸入 剪刀/石頭/布", ephemeral=True)
            return
        bot_choice = random.choice(list(self.rps_choices.keys()))
        if choice == bot_choice:
            result = "平手 🤝"
        elif (choice=="剪刀" and bot_choice=="布") or (choice=="石頭" and bot_choice=="剪刀") or (choice=="布" and bot_choice=="石頭"):
            result = "你贏了 🎉"
        else:
            result = "你輸了 😢"
        await interaction.response.send_message(f"你出 {self.rps_choices[choice]} ({choice})\n我出 {self.rps_choices[bot_choice]} ({bot_choice})\n結果：{result}")

    @app_commands.command(name="draw", description="隨機抽選一個選項")
    @app_commands.describe(options="輸入多個選項，用逗號或空格分隔")
    async def draw(self, interaction: discord.Interaction, options: str):
        if "," in options:
            items = [o.strip() for o in options.split(",") if o.strip()]
        else:
            items = [o.strip() for o in options.split() if o.strip()]

        if len(items) < 2:
            await interaction.response.send_message("❌ 請至少輸入兩個選項", ephemeral=True)
            return

        winner = random.choice(items)
        await interaction.response.send_message(f"🎉 抽選結果：**{winner}**")

# =========================
# ⚡ Cog: Ping 指令
# =========================
class PingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="檢查機器人延遲")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)  # 轉成毫秒
        await interaction.response.send_message(f"🏓 Pong! 延遲：{latency_ms}ms")
        
# =========================
# ⚡ Cog: 抽獎
# =========================
class DrawCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_draws = {}  # key: guild_id, value: dict(name, max_winners, participants, task, end_time)

    # 解析時間字串，支援 10s / 5m / 1h
    def parse_duration(self, timestr: str) -> int:
        pattern = r"(\d+)([smh])"
        match = re.fullmatch(pattern, timestr.strip().lower())
        if not match:
            raise ValueError("時間格式錯誤，範例: 10s, 5m, 1h")
        number, unit = match.groups()
        number = int(number)
        return number * {"s":1,"m":60,"h":3600}[unit]

    @app_commands.command(name="start_draw", description="開始抽獎")
    @app_commands.describe(
        name="抽獎名稱",
        max_winners="最多中獎人數（預設 1）",
        duration="抽獎持續時間，例如：10s / 5m / 1h（預設 60s）"
    )
    async def start_draw(self, interaction: discord.Interaction, name: str, max_winners: int = 1, duration: str = "60s"):
        guild_id = interaction.guild.id
        if guild_id in self.active_draws:
            await interaction.response.send_message("❌ 本伺服器已有正在進行的抽獎", ephemeral=True)
            return

        try:
            seconds = self.parse_duration(duration)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        end_time = asyncio.get_event_loop().time() + seconds
        draw_info = {
            "name": name,
            "max_winners": max_winners,
            "participants": set(),
            "task": asyncio.create_task(self._auto_end_draw(interaction, guild_id, seconds)),
            "end_time": end_time
        }
        self.active_draws[guild_id] = draw_info
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

    @app_commands.command(name="draw_status", description="查看抽獎狀態")
    async def draw_status(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if guild_id not in self.active_draws:
            await interaction.response.send_message("❌ 沒有正在進行的抽獎", ephemeral=True)
            return
        draw = self.active_draws[guild_id]
        remaining = max(0, int(draw["end_time"] - asyncio.get_event_loop().time()))
        await interaction.response.send_message(
            f"🎯 抽獎 `{draw['name']}`\n參加人數：{len(draw['participants'])}\n剩餘時間：{remaining} 秒",
            ephemeral=True
        )

    @app_commands.command(name="cancel_draw", description="取消抽獎（管理員限定）")
    async def cancel_draw(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 你沒有權限取消抽獎", ephemeral=True)
            return
        guild_id = interaction.guild.id
        if guild_id not in self.active_draws:
            await interaction.response.send_message("❌ 沒有正在進行的抽獎", ephemeral=True)
            return
        draw = self.active_draws.pop(guild_id)
        draw["task"].cancel()
        await interaction.response.send_message(f"⚠️ 抽獎 `{draw['name']}` 已被取消", ephemeral=False)

    async def _auto_end_draw(self, interaction, guild_id, duration_seconds):
        try:
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
        except asyncio.CancelledError:
            # 抽獎被取消
            return

# =========================
# ⚡ Cog: 公告
# =========================
class AnnounceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="announce", description="發布公告（管理員限定）")
    @app_commands.describe(
        title="公告標題",
        content="公告內容",
        channel="公告頻道（可不選）",
        ping_everyone="是否要 @everyone"
    )
    async def announce(self, interaction: discord.Interaction, title: str, content: str, channel: discord.TextChannel = None, ping_everyone: bool = False):
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

# =========================
# ⚡ HTTP 保活
# =========================
async def keep_alive():
    async def handle(request):
        return web.Response(text="Bot is running!")
    app = web.Application()
    app.add_routes([web.get("/", handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port=int(os.getenv("PORT", 8080)))
    await site.start()
    print("✅ HTTP server running on port 8080")

# =========================
# ⚡ Bot 啟動
# =========================
# 在 Bot 啟動區域
@bot.event
async def on_ready():
    print(f"✅ Bot 已啟動！登入身分：{bot.user}")
    await bot.tree.sync()  # 同步 Slash commands

async def main():
    # 啟動 HTTP server
    await keep_alive()

    # 註冊 Cogs
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(FunCog(bot))
    await bot.add_cog(DrawCog(bot))
    await bot.add_cog(AnnounceCog(bot))

    # 啟動 Bot
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⚡ Bot 已停止")