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

    @app_commands.command(name="say", description="讓機器人發送訊息（可發頻道或私訊單一用戶）")
    @app_commands.describe(
        message="要發送的訊息",
        channel="選擇要發送的頻道（可選，不選則預設為當前頻道）",
        user="選擇要私訊的使用者（可選）"
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: str,
        channel: discord.TextChannel = None,
        user: discord.User = None
    ):
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
    async def calc(self, interaction: discord.Interaction, expr: str):
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
    async def delete(
        self,
        interaction: discord.Interaction,
        amount: int
    ):
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
            
#=========================
# ⚡ Cog: 反應身分組
# =========================
class ReactionRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reaction_roles = {}

    @app_commands.command(name="reactionrole", description="新增反應身分組（管理員用）")
    @app_commands.describe(
        message="要反應的訊息文字或訊息連結",
        emoji="對應的表情符號",
        role="要給的身分組",
        channel="訊息所在頻道（可不選）"
    )
    async def reactionrole(
        self,
        interaction: discord.Interaction,
        message: str,
        emoji: str,
        role: discord.Role,
        channel: Optional[discord.TextChannel] = None
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有管理員可以使用此指令", ephemeral=True)
            return

        msg_obj = None
        if re.match(r"https?://", message):
            try:
                m = re.match(r"https?://discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", message)
                if not m:
                    raise ValueError
                guild_id, channel_id, message_id = map(int, m.groups())
                channel_obj = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                msg_obj = await channel_obj.fetch_message(message_id)
            except Exception:
                await interaction.response.send_message("❌ 無法解析訊息連結", ephemeral=True)
                return
        else:
            if channel is None:
                channel = interaction.channel
            try:
                async for msg in channel.history(limit=100):
                    if msg.content == message:
                        msg_obj = msg
                        break
                if msg_obj is None:
                    await interaction.response.send_message("❌ 找不到符合的訊息", ephemeral=True)
                    return
            except Exception:
                await interaction.response.send_message("❌ 無法取得頻道訊息", ephemeral=True)
                return

        try:
            await msg_obj.add_reaction(emoji)
        except Exception as e:
            await interaction.response.send_message(f"❌ 無法加反應: {e}", ephemeral=True)
            return

        guild_roles = self.reaction_roles.setdefault(interaction.guild_id, {})
        msg_roles = guild_roles.setdefault(msg_obj.id, {})
        msg_roles[emoji] = role.id

        await interaction.response.send_message(f"✅ 已設定 {emoji} -> {role.name} 的反應身分組", ephemeral=True)

    @app_commands.command(name="removereactionrole", description="刪除反應身分組（管理員用）")
    @app_commands.describe(
        message="訊息文字或訊息連結",
        emoji="對應的表情符號",
        channel="訊息所在頻道（可不選）"
    )
    async def removereactionrole(
        self,
        interaction: discord.Interaction,
        message: str,
        emoji: str,
        channel: Optional[discord.TextChannel] = None
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有管理員可以使用此指令", ephemeral=True)
            return

        msg_obj = None
        if re.match(r"https?://", message):
            try:
                m = re.match(r"https?://discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", message)
                if not m:
                    raise ValueError
                guild_id, channel_id, message_id = map(int, m.groups())
                channel_obj = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                msg_obj = await channel_obj.fetch_message(message_id)
            except Exception:
                await interaction.response.send_message("❌ 無法解析訊息連結", ephemeral=True)
                return
        else:
            if channel is None:
                channel = interaction.channel
            try:
                async for msg in channel.history(limit=100):
                    if msg.content == message:
                        msg_obj = msg
                        break
                if msg_obj is None:
                    await interaction.response.send_message("❌ 找不到符合的訊息", ephemeral=True)
                    return
            except Exception:
                await interaction.response.send_message("❌ 無法取得頻道訊息", ephemeral=True)
                return

        guild_roles = self.reaction_roles.get(interaction.guild_id, {})
        msg_roles = guild_roles.get(msg_obj.id, {})
        if emoji in msg_roles:
            del msg_roles[emoji]
            await interaction.response.send_message(f"✅ 已移除 {emoji} 的反應身分組", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 找不到該反應身分組設定", ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        guild_id = payload.guild_id
        if guild_id not in self.reaction_roles:
            return
        guild_roles = self.reaction_roles[guild_id]
        if payload.message_id not in guild_roles:
            return
        msg_roles = guild_roles[payload.message_id]
        if str(payload.emoji) not in msg_roles:
            return
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        role_id = msg_roles[str(payload.emoji)]
        role = guild.get_role(role_id)
        if role:
            try:
                await member.add_roles(role)
            except:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        guild_id = payload.guild_id
        if guild_id not in self.reaction_roles:
            return
        guild_roles = self.reaction_roles[guild_id]
        if payload.message_id not in guild_roles:
            return
        msg_roles = guild_roles[payload.message_id]
        if str(payload.emoji) not in msg_roles:
            return
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        role_id = msg_roles[str(payload.emoji)]
        role = guild.get_role(role_id)
        if role:
            try:
                await member.remove_roles(role)
            except:
                pass

# =========================
# ⚡ Cog: 遊戲指令
# =========================
class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_games = {}

    @app_commands.command(name="rps_invite", description="邀請玩家剪刀石頭布對戰")
    @app_commands.describe(
        rounds="局數 (預設 3)",
        opponent="指定玩家 (可選，不選則所有人可加入)",
        vs_bot="是否與機器人對戰"
    )
    async def rps_invite(self, interaction, rounds: int = 3, opponent: discord.Member = None, vs_bot: bool = True):
        guild_id = interaction.guild.id
        if guild_id not in self.active_games:
            self.active_games[guild_id] = []

        view = RPSView(self.bot, rounds, opponent, vs_bot)
        self.active_games[guild_id].append(view)

        opponent_text = f"{opponent.mention}" if opponent else "所有人"
        # ✅ 在這裡立即回應，避免未受回應
        await interaction.response.send_message(
            f"🎮 {opponent_text}，有 {rounds} 局的剪刀石頭布對戰邀請！\n按下加入開始遊戲，按取消結束邀請。",
            view=view
        )

class RPSView(ui.View):
    def __init__(self, bot, rounds: int, opponent: discord.Member = None, vs_bot: bool = True):
        super().__init__(timeout=None)
        self.bot = bot
        self.rounds = rounds
        self.opponent = opponent
        self.vs_bot = vs_bot
        self.players = {}
        self.current_round = 0
        self.message = None

    async def interaction_check(self, interaction):
        if self.opponent and interaction.user != self.opponent:
            await interaction.response.send_message("❌ 你不是被邀請的人", ephemeral=True)
            return False
        return True

    @ui.button(label="加入", style=discord.ButtonStyle.green)
    async def join_game(self, interaction, button):
        if interaction.user in self.players:
            await interaction.response.send_message("你已加入遊戲", ephemeral=True)
            return
        self.players[interaction.user] = None
        await interaction.response.send_message(f"✅ {interaction.user.mention} 已加入遊戲", ephemeral=True)
        if self.vs_bot and not self.opponent:
            self.players[self.bot.user] = None

    @ui.button(label="取消", style=discord.ButtonStyle.red)
    async def cancel_game(self, interaction, button):
        await interaction.message.edit(content="❌ 遊戲已取消", view=None)
        await interaction.response.send_message("遊戲已取消", ephemeral=True)
        self.stop()

# =========================
# ⚡ Cog: Ping 指令
# =========================
class PingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="檢查機器人延遲")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! 延遲：{latency_ms}ms")

# 其他 Cogs (DrawCog, AnnounceCog) 保持不變，只修改涉及長時間互動的地方，已安全。

# =========================
# ⚡ Bot 啟動
# =========================
@bot.event
async def on_ready():
    print(f"✅ Bot 已啟動！登入身分：{bot.user}")
    await bot.tree.sync()

# 保活 HTTP
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
    
    # 在程式結束時關閉
    async def shutdown():
        await runner.cleanup()
    
    return shutdown

async def main():
    # 啟動 HTTP 保活
    await keep_alive()

    # 註冊 Cogs
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(FunCog(bot))
    await bot.add_cog(DrawCog(bot))
    await bot.add_cog(AnnounceCog(bot))
    await bot.add_cog(PingCog(bot))
    await bot.add_cog(ReactionRoleCog(bot))

    # 啟動 Bo
shutdown_keep_alive = await keep_alive()

try:
    await bot.start(TOKEN)
finally:
    await shutdown_keep_alive()  # 確保 HTTP server 關閉閉



    except discord.errors.HTTPException as e:
        if e.status == 429:
            print("⚠️ 觸發 Discord 429 限制")
        else:
            print(f"❌ HTTP 錯誤：{e}")
        sys.exit(1)
    except discord.LoginFailure:
        print("❌ Token 錯誤")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 未知錯誤：{e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()
    await bot.start(TOKEN)