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
import datetime

command_logs = []  # 紀錄所有指令使用

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

#剪刀石頭布參數
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
            self.scores[self.player1] += 1
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
# ⚡ COGS
# =========================

# -------- UtilityCog --------
class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    # ================
    # /say 指令
    # ================  
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
        await log_command(interaction, "/say")  # ✅ 放在函式內最開頭

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
        await log_command(interaction, "/announce")  # ✅ 放在函式最上面

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
    @app_commands.describe(expr="例如：1+2*3")
    async def calc(self, interaction: Interaction, expr: str):
        await log_command(interaction.user, "/calc")
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
        await log_command(interaction.user, "/delete")
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
        await log_command(interaction.user, "/reactionrole")
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
# -------- FunCog --------
class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_games = {}

    # 🎮 剪刀石頭布
    @app_commands.command(name="rps", description="剪刀石頭布對戰")
    @app_commands.describe(
        rounds="搶幾勝（預設 3）",
        opponent="要挑戰的對象（可選）",
        vs_bot="是否與機器人對戰（預設 False）"
    )
    async def rps(
        await log_command(interaction.user, "/rps")
        self,
        interaction: discord.Interaction,  # 正確的型別
        rounds: int = 3,
        opponent: discord.User = None,
        vs_bot: bool = False
    ):
        await log_command(interaction, "/rps")
        if not opponent and not vs_bot:
            await interaction.response.send_message(
                "❌ 你必須選擇對手或開啟 vs_bot!", ephemeral=True
            )
            return

        if opponent and opponent.bot:
            await interaction.response.send_message(
                "🤖 不能邀請機器人，請改用 vs_bot=True", ephemeral=True
            )
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

        # 玩家同意後開始遊戲
        view = RPSView(interaction.user, opponent, rounds, vs_bot)
        embed = view.make_embed()
        view.message = await interaction.followup.send(embed=embed, view=view)

    # 🎲 擲骰子
    @app_commands.command(name="dice", description="擲一顆 1-6 的骰子")
    async def dice(self, interaction: discord.Interaction):
        await log_command(interaction.user, "/dice")
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
        await log_command(interaction.user, "/start_draw")
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
        await log_command(interaction.user, "/ping")
        await interaction.response.send_message(f"🏓 Pong! 延遲：{round(self.bot.latency*1000)}ms")
        
#—————————helpCog——————————     
        
class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="顯示所有可用的指令")
    async def help(self, interaction: discord.Interaction):
        await log_command(interaction.user, "/help")
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



@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    await interaction.response.send_message(f"❌ 指令錯誤：{error}", ephemeral=True)

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command):
    command_logs.append({
        "user": str(interaction.user),
        "command": f"/{command.qualified_name}",
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# =========================
# ⚡ Bot 啟動 & HTTP 保活 and 網頁
# =========================
# ====== 指令使用紀錄系統 ======
# ====== 指令使用紀錄系統 ======
import threading
from flask import Flask
import discord

command_logs = []  # [{text, time}]

async def log_command(interaction: discord.Interaction, command: str):
    from datetime import datetime
    guild_name = interaction.guild.name if interaction.guild else "私人訊息"
    log_text = f"📝 {interaction.user} 在伺服器「{guild_name}」使用了 {command}"
    command_logs.append({
        "text": log_text,
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    })
    if len(command_logs) > 100:
        command_logs.pop(0)

# ====== Flask 網頁 (HTML 格式) ======
app = Flask(__name__)

@app.route("/")
def index():
    rows = "".join(
        f"<tr><td>{log['time']}</td><td>{log['text']}</td></tr>"
        for log in reversed(command_logs)
    )
    return f"""
    <html>
        <head><title>指令紀錄</title></head>
        <body style="font-family: sans-serif;">
            <h1>📜 Discord Bot 指令使用紀錄</h1>
            <table border="1" cellspacing="0" cellpadding="6">
                <tr><th>時間</th><th>紀錄</th></tr>
                {rows if rows else "<tr><td colspan='2'>目前沒有紀錄</td></tr>"}
            </table>
        </body>
    </html>
    """

def run_web():
    app.run(host="0.0.0.0", port=8080)

def keep_web_alive():
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()


async def main():
    keep_web_alive()
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