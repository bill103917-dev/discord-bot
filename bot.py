import os
import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import re
from typing import List, Optional
from aiohttp import web
from discord import ui
from discord import Interaction
from discord import TextChannel, User, Message
from discord import Interaction, User, ui
from discord import ui, Interaction
from typing import Optional
import sys

# =========================
# ⚡ 基本設定
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN or TOKEN.strip() == "" or TOKEN.startswith(" "):
    print("❌ TOKEN 沒有正確設定，請到環境變數檢查！")
    sys.exit(1)

OWNER_ID = 1238436456041676853
SPECIAL_USER_IDS = [OWNER_ID]

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

MAIN_BOT_ID = int(os.environ.get("MAIN_BOT_ID", 0))
def is_main_instance():
    return bot.user.id == MAIN_BOT_ID or MAIN_BOT_ID == 0

# =========================
# ⚡ COGS
# =========================

# -------- UtilityCog --------
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="say", description="讓機器人發送訊息（可發頻道或私訊單一用戶）")
    @app_commands.describe(
        message="要發送的訊息",
        channel="選擇要發送的頻道（可選，不選則預設為當前頻道）",
        user="選擇要私訊的使用者（可選）"
    )
    async def say(self, interaction: Interaction, message: str, channel: discord.TextChannel = None, user: discord.User = None):
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            return

        if user:
            try:
                await user.send(message)
                await interaction.response.send_message(f"✅ 已私訊給 {user.mention}", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ 發送失敗: {e}", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        try:
            await target_channel.send(message)
            await interaction.response.send_message(f"✅ 已在 {target_channel.mention} 發送訊息", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 發送失敗: {e}", ephemeral=True)

    @app_commands.command(name="calc", description="簡單計算器")
    @app_commands.describe(expr="例如：1+2*3")
    async def calc(self, interaction: Interaction, expr: str):
        try:
            allowed = "0123456789+-*/(). "
            if not all(c in allowed for c in expr):
                raise ValueError("包含非法字符")
            result = eval(expr)
            await interaction.response.send_message(f"結果：{result}")
        except Exception as e:
            await interaction.response.send_message(f"計算錯誤：{e}")

    @app_commands.command(name="delete", description="刪除訊息（管理員限定）")
    @app_commands.describe(amount="要刪除的訊息數量（1~100）")
    async def delete(self, interaction: Interaction, amount: int):
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


# -------- ReactionRoleCog --------
class ReactionRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reaction_roles = {}

    @app_commands.command(name="reactionrole", description="新增反應身分組（管理員用）")
    @app_commands.describe(
        message="訊息文字或連結",
        emoji="表情符號",
        role="身分組",
        channel="頻道（可選）"
    )
    async def reactionrole(self, interaction: Interaction, message: str, emoji: str, role: discord.Role, channel: Optional[discord.TextChannel] = None):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有管理員可以使用此指令", ephemeral=True)
            return

        msg_obj = None
        if re.match(r"https?://", message):
            try:
                m = re.match(r"https?://discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", message)
                guild_id, channel_id, message_id = map(int, m.groups())
                channel_obj = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                msg_obj = await channel_obj.fetch_message(message_id)
            except:
                await interaction.response.send_message("❌ 無法解析訊息連結", ephemeral=True)
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

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        guild_roles = self.reaction_roles.get(payload.guild_id, {})
        msg_roles = guild_roles.get(payload.message_id, {})
        role_id = msg_roles.get(str(payload.emoji))
        if not role_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if member and not member.bot:
            role = guild.get_role(role_id)
            if role:
                try: await member.add_roles(role)
                except: pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        guild_roles = self.reaction_roles.get(payload.guild_id, {})
        msg_roles = guild_roles.get(payload.message_id, {})
        role_id = msg_roles.get(str(payload.emoji))
        if not role_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if member and not member.bot:
            role = guild.get_role(role_id)
            if role:
                try: await member.remove_roles(role)
                except: pass

# -------- FunCog --------
class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_games = {}

# （剪刀石頭布等遊戲指令可照你之前貼的程式加入此 Cog）


# -------- DrawCog --------
class DrawCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_draws = {}

    def parse_duration(self, timestr: str) -> int:
        pattern = r"(\d+)([smh])"
        match = re.fullmatch(pattern, timestr.strip().lower())
        if not match:
            raise ValueError("時間格式錯誤")
        number, unit = match.groups()
        return int(number) * {"s":1,"m":60,"h":3600}[unit]

    @app_commands.command(name="start_draw", description="開始抽獎")
    async def start_draw(self, interaction: Interaction, name: str, max_winners: int = 1, duration: str = "60s"):
        guild_id = interaction.guild.id
        if guild_id in self.active_draws:
            await interaction.response.send_message("❌ 本伺服器已有抽獎", ephemeral=True)
            return
        try:
            seconds = self.parse_duration(duration)
        except Exception as e:
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
        await interaction.response.send_message(f"🎉 抽獎 `{name}` 已開始！使用 /join_draw 參加。名額: {max_winners}。")

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
            return


# -------- PingCog --------
class PingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @app_commands.command(name="ping", description="檢查機器人延遲")
    async def ping(self, interaction: Interaction):
        await interaction.response.send_message(f"🏓 Pong! 延遲：{round(self.bot.latency*1000)}ms")

# -------- AnnounceCog --------
class AnnounceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    @app_commands.command(name="announce", description="發布公告（管理員限定）")
    async def announce(self, interaction: Interaction, title: str, content: str, channel: discord.TextChannel = None, ping_everyone: bool = False):
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
# ⚡ Bot 啟動 & HTTP 保活
# =========================
@bot.event
async def on_ready():
    print(f"✅ Bot 已啟動！登入身分：{bot.user}")
    await bot.tree.sync()

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
    async def shutdown():
        await runner.cleanup()
    return shutdown

async def main():
    shutdown_keep_alive = await keep_alive()
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(FunCog(bot))
    await bot.add_cog(DrawCog(bot))
    await bot.add_cog(PingCog(bot))
    await bot.add_cog(AnnounceCog(bot))
    await bot.add_cog(ReactionRoleCog(bot))

    try:
        await bot.start(TOKEN)
    finally:
        await shutdown_keep_alive()

if __name__ == "__main__":
    asyncio.run(main())