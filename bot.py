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
import asyncio
import random

class RPSView(discord.ui.View):
    def __init__(self, player1, player2, rounds, vs_bot):
        super().__init__(timeout=60)
        self.player1 = player1
        self.player2 = player2
        self.rounds = rounds
        self.vs_bot = vs_bot
        self.scores = {player1.id: 0}
        if vs_bot:
            self.scores["bot"] = 0
            self.choices = {player1.id: None, "bot": random.choice(["✌️", "👊", "🖐️"])}
        else:
            self.scores[player2.id] = 0
            self.choices = {player1.id: None, player2.id: None}

    def make_embed(self):
        embed = discord.Embed(title=f"✂️ 石頭剪刀布（搶 {self.rounds} 勝）", color=discord.Color.blue())
        desc = []
        for pid in self.choices:
            name = "🤖 機器人" if pid == "bot" else f"<@{pid}>"
            choice = self.choices[pid]
            if choice is None:
                desc.append(f"{name}: ❓")
            elif choice in ["✌️", "👊", "🖐️"]:
                desc.append(f"{name}: ✅")
        embed.description = "\n".join(desc)
        score_text = " | ".join([f"{'🤖 機器人' if k=='bot' else f'<@{k}>'}: {v}" for k,v in self.scores.items()])
        embed.add_field(name="比分", value=score_text, inline=False)
        return embed

    async def handle_choice(self, interaction, choice):
        if interaction.user.id not in self.choices:
            await interaction.response.send_message("⛔ 你不是這場比賽的玩家！", ephemeral=True)
            return
        if self.choices[interaction.user.id] is not None:
            await interaction.response.send_message("⏳ 你已經出過拳了！", ephemeral=True)
            return

        self.choices[interaction.user.id] = choice
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

        # 如果雙方都出完
        if all(v is not None for v in self.choices.values()):
            await self.end_round(interaction)

    async def end_round(self, interaction):
        # 顯示雙方真實出拳
        reveal_embed = discord.Embed(title=f"✂️ 石頭剪刀布（搶 {self.rounds} 勝）", color=discord.Color.green())
        desc = []
        for pid, choice in self.choices.items():
            name = "🤖 機器人" if pid == "bot" else f"<@{pid}>"
            desc.append(f"{name}: {choice}")
        reveal_embed.description = "\n".join(desc)
        await interaction.message.edit(embed=reveal_embed, view=None)

        await asyncio.sleep(1)

        # 判斷勝負
        winner = self.check_winner()
        if winner:
            self.scores[winner] += 1

        # 檢查是否結束
        if any(score >= self.rounds for score in self.scores.values()):
            winner_id = max(self.scores, key=self.scores.get)
            winner_name = "🤖 機器人" if winner_id == "bot" else f"<@{winner_id}>"
            await interaction.followup.send(f"🎉 {winner_name} 獲勝！")
            self.stop()
            return

        # 下一局
        self.choices = {k: None for k in self.choices}
        if self.vs_bot:
            self.choices["bot"] = random.choice(["✌️", "👊", "🖐️"])
        await interaction.message.edit(embed=self.make_embed(), view=self)

    def check_winner(self):
        players = list(self.choices.keys())
        p1, p2 = players[0], players[1]
        c1, c2 = self.choices[p1], self.choices[p2]
        if c1 == c2:
            return None
        wins = {"✌️": "🖐️", "👊": "✌️", "🖐️": "👊"}
        return p1 if wins[c1] == c2 else p2

    @discord.ui.button(label="✌️ 剪刀", style=discord.ButtonStyle.primary)
    async def scissor(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "✌️")

    @discord.ui.button(label="👊 石頭", style=discord.ButtonStyle.danger)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "👊")

    @discord.ui.button(label="🖐️ 布", style=discord.ButtonStyle.success)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    @app_commands.command(name="rps", description="剪刀石頭布對戰")
    @app_commands.describe(rounds="搶幾勝（預設 3）", opponent="挑戰對象", vs_bot="是否跟機器人玩")
    async def rps(self, interaction: discord.Interaction, rounds: int = 3, opponent: discord.User = None, vs_bot: bool = False):
        if not opponent and not vs_bot:
            await interaction.response.send_message("❌ 請選擇對手或啟用 vs_bot", ephemeral=True)
            return
        if opponent and opponent.bot:
            await interaction.response.send_message("🤖 不能邀請機器人，請改用 vs_bot=True", ephemeral=True)
            return

        # 如果是對玩家，先詢問是否接受
        if opponent:
            view = ConfirmView()
            await interaction.response.send_message(f"👋 <@{opponent.id}> 你被 <@{interaction.user.id}> 邀請來玩剪刀石頭布，搶 {rounds} 勝！是否接受？", view=view)
            timeout = await view.wait()
            if timeout or not view.value:
                await interaction.followup.send("❌ 對方拒絕或未回應，遊戲取消。")
                return

        view = RPSView(interaction.user, opponent, rounds, vs_bot)
        await interaction.followup.send(embed=view.make_embed(), view=view)


class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self.value = None

    @discord.ui.button(label="✅ 接受", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.edit_message(content="✅ 已接受挑戰！", view=None)
        self.stop()

    @discord.ui.button(label="❌ 拒絕", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.edit_message(content="❌ 拒絕挑戰，遊戲取消。", view=None)
        self.stop()
        
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