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

#剪刀石頭布參數
import discord
from discord import app_commands
from discord.ext import commands
import random

CHOICES = ["✌️", "✊", "🖐️"]  # 剪刀、石頭、布

class RPSView(discord.ui.View):
    def __init__(self, challenger, opponent=None, rounds=3, vs_bot=False):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.opponent = opponent
        self.rounds = rounds
        self.vs_bot = vs_bot

        self.scores = {challenger.id: 0}
        if opponent:
            self.scores[opponent.id] = 0
        else:
            self.scores["bot"] = 0

        self.choices = {}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 限制只能挑戰者和被邀請的人點
        if self.opponent and interaction.user.id not in (self.challenger.id, self.opponent.id):
            await interaction.response.send_message("⛔ 你不是對戰雙方，不能參與喔！", ephemeral=True)
            return False
        elif not self.opponent and interaction.user.id != self.challenger.id:
            await interaction.response.send_message("⛔ 這場是單人對機器人，你不能參與！", ephemeral=True)
            return False
        return True

    async def button_callback(self, interaction: discord.Interaction, choice: str):
        self.choices[interaction.user.id] = choice

        # 如果是對機器人，馬上出
        if self.vs_bot:
            bot_choice = random.choice(CHOICES)
            self.choices["bot"] = bot_choice
            await self.finish_round(interaction)
        else:
            # 雙方都選完再判斷
            if len(self.choices) == 2:
                await self.finish_round(interaction)

    async def finish_round(self, interaction: discord.Interaction):
        c1 = self.choices.get(self.challenger.id)
        c2 = self.choices.get(self.opponent.id if self.opponent else "bot")
        result = self.check_winner(c1, c2)

        if result == "challenger":
            self.scores[self.challenger.id] += 1
        elif result == "opponent":
            key = self.opponent.id if self.opponent else "bot"
            self.scores[key] += 1

        self.choices.clear()

        # 更新 Embed 顯示
        embed = self.make_embed(c1, c2)
        await interaction.message.edit(embed=embed, view=self)

        # 判斷是否結束
        if max(self.scores.values()) >= self.rounds:
            self.stop()

    def check_winner(self, c1, c2):
        wins = {"✌️": "🖐️", "🖐️": "✊", "✊": "✌️"}
        if c1 == c2:
            return "tie"
        elif wins[c1] == c2:
            return "challenger"
        else:
            return "opponent"

    def make_embed(self, c1=None, c2=None):
        title = "✊ 猜拳"
        desc = f"搶 {self.rounds} 勝\n\n玩家：\n"
        desc += f"{self.challenger.mention}"
        if self.opponent:
            desc += f" vs {self.opponent.mention}"
        else:
            desc += " vs 🤖 **機器人**"

        score_str = f"\n\n比數： {self.scores[self.challenger.id]} - {self.scores[self.opponent.id if self.opponent else 'bot']}"

        round_result = ""
        if c1 and c2:
            round_result = f"\n\n{self.challenger.mention} 出 {c1}，" + \
                           (f"{self.opponent.mention}" if self.opponent else "🤖 機器人") + f" 出 {c2}"

        embed = discord.Embed(title=title, description=desc + score_str + round_result, color=0x00ff99)
        return embed

    @discord.ui.button(label="✌️", style=discord.ButtonStyle.primary)
    async def scissor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.button_callback(interaction, "✌️")

    @discord.ui.button(label="✊", style=discord.ButtonStyle.primary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.button_callback(interaction, "✊")

    @discord.ui.button(label="🖐️", style=discord.ButtonStyle.primary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.button_callback(interaction, "🖐️")
# =========================
# ⚡ COGS
# =========================

# -------- UtilityCog --------
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    # ================
    # /say 指令
    # ================  
    @app_commands.command(
        name="say",
        description="讓機器人發送訊息（管理員或特殊使用者限定）"
    )
    @app_commands.describe(
        message="要發送的訊息",
        channel="選擇要發送的頻道（可選，不選則預設為當前頻道）",
        user="選擇要私訊的使用者（可選）"
    )
    async def say(
        self,
        interaction: discord.Interaction,
        message: str,
        channel: Optional[discord.TextChannel] = None,
        user: Optional[discord.User] = None
    ):
        # ✅ 權限檢查（管理員 或 特殊使用者）
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            return

        # 先回應避免超時
        await interaction.response.defer(ephemeral=True)

        # 如果有指定用戶 -> 發私訊
        if user:
            try:
                await user.send(message)
                await interaction.followup.send(f"✅ 已私訊給 {user.mention}", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ 發送失敗: {e}", ephemeral=True)
            return

        # 如果沒指定用戶 -> 發頻道
        target_channel = channel or interaction.channel
        try:
            await target_channel.send(message)
            await interaction.followup.send(f"✅ 已在 {target_channel.mention} 發送訊息", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 發送失敗: {e}", ephemeral=True)
    # ================
    # /公告 指令
    # ================
    @app_commands.command(
        name="announce",
        description="發布公告（管理員限定）"
    )
    @app_commands.describe(
        title="公告標題（可選）",
        content="公告內容",
        channel="公告頻道（可不選）",
        ping_everyone="是否要 @everyone"
    )
    async def announce(
        self,
        interaction: discord.Interaction,
        content: str,
        title: Optional[str] = "公告📣",
        channel: Optional[discord.TextChannel] = None,
        ping_everyone: bool = False
    ):
        # 先回應，避免超時
        await interaction.response.defer(ephemeral=True)

        # 權限檢查
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

        # 發送公告
        mention = "@everyone" if ping_everyone else ""
        await target_channel.send(content=mention, embed=embed)
        await interaction.followup.send(f"✅ 公告已發送到 {target_channel.mention}", ephemeral=True)

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

    # 🎮 剪刀石頭布
    @app_commands.command(name="rps", description="剪刀石頭布")
    @app_commands.describe(
        rounds="搶幾勝 (預設 3)",
        opponent="要挑戰的對象 (可選)",
        vs_bot="是否對機器人玩 (可選)"
    )
    async def rps(
        self,
        interaction: discord.Interaction,
        rounds: int | None = None,
        opponent: discord.User | None = None,
        vs_bot: bool | None = None
    ):
        # 如果使用者什麼都沒填，直接報錯
        if opponent is None and vs_bot is None:
            await interaction.response.send_message("⚠️ 你必須指定一個對手或選擇 vs_bot=True！", ephemeral=True)
            return

        # rounds 預設值
        rounds = rounds or 3

        # 防止邀請機器人
        if opponent and opponent.bot:
            await interaction.response.send_message("🤖 不能邀請機器人，請改用 vs_bot=True", ephemeral=True)
            return

        view = RPSView(interaction.user, opponent, rounds, vs_bot or False)
        embed = view.make_embed()
        await interaction.response.send_message(embed=embed, view=view)

    # 🎲 擲骰子
    @app_commands.command(name="dice", description="擲一顆 1-6 的骰子")
    async def dice(self, interaction: discord.Interaction):
        number = random.randint(1, 6)
        await interaction.response.send_message(f"🎲 {interaction.user.mention} 擲出了 **{number}**！")




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
        
#—————————helpCog——————————     
        
class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="顯示所有可用的指令")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 指令清單",
            description="以下是目前可用的指令：",
            color=discord.Color.blue()
        )

        # 讀取 bot.tree 裡所有指令
        commands_list = self.bot.tree.get_commands()

        for cmd in commands_list:
            embed.add_field(
                name=f"/{cmd.name}",
                value=cmd.description or "沒有描述",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)



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
    await bot.add_cog(ReactionRoleCog(bot))
    await bot.add_cog(HelpCog(bot))

    try:
        await bot.start(TOKEN)
    finally:
        await shutdown_keep_alive()

if __name__ == "__main__":
    asyncio.run(main())