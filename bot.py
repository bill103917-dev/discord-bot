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
        # 權限檢查
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            return

        # 如果有指定用戶 -> 發私訊
        if user:
            try:
                await user.send(message)
                await interaction.response.send_message(f"✅ 已私訊給 {user.mention}", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ 發送失敗: {e}", ephemeral=True)
            return

        # 如果沒指定用戶 -> 發頻道
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

    # === /delete 指令 ===   👈 把這段放進來
    @app_commands.command(name="delete", description="刪除訊息（管理員限定）")
    @app_commands.describe(
        amount="要刪除的訊息數量（1~100）"
    )
    async def delete(
        self,
        interaction: discord.Interaction,
        amount: int
    ):
        # ✅ 只有管理員 或 SPECIAL_USER_IDS 可以用
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in SPECIAL_USER_IDS:
            await interaction.response.send_message("❌ 只有管理員可以刪除訊息", ephemeral=True)
            return

        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ 請輸入 1 ~ 100 的數字", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            deleted = await interaction.channel.purge(limit=amount+1)  # +1 把指令那則也刪掉
            await interaction.followup.send(f"✅ 已刪除 {len(deleted)-1} 則訊息", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 刪除失敗: {e}", ephemeral=True)
            
#=========================
# ⚡ Cog: 反應身分組 (訊息連結版, 中文化)
# =========================
from typing import Optional
import discord
from discord.ext import commands
from discord import app_commands
import re

class ReactionRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 儲存每個伺服器的反應身分組設定
        # 格式: {guild_id: {message_id: {"emoji": role_id}}}
        self.reaction_roles = {}

    # 新增反應身分組
    @app_commands.command(
        name="reactionrole",
        description="新增反應身分組（管理員用）"
    )
    @app_commands.describe(
        message="要反應的訊息文字或訊息連結",
        emoji="對應的表情符號",
        role="要給的身分組",
        channel="訊息所在頻道（可不選，用訊息連結即可）"
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

        # 嘗試解析訊息連結
        msg_obj = None
        if re.match(r"https?://", message):
            try:
                # 例如連結格式: https://discord.com/channels/{guild_id}/{channel_id}/{message_id}
                m = re.match(r"https?://discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", message)
                if not m:
                    raise ValueError
                guild_id, channel_id, message_id = map(int, m.groups())
                channel_obj = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                msg_obj = await channel_obj.fetch_message(message_id)
            except Exception:
                await interaction.response.send_message("❌ 無法解析訊息連結，請確認格式正確", ephemeral=True)
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

        # 加入反應
        try:
            await msg_obj.add_reaction(emoji)
        except Exception as e:
            await interaction.response.send_message(f"❌ 無法加反應: {e}", ephemeral=True)
            return

        # 儲存設定
        guild_roles = self.reaction_roles.setdefault(interaction.guild_id, {})
        msg_roles = guild_roles.setdefault(msg_obj.id, {})
        msg_roles[emoji] = role.id

        await interaction.response.send_message(f"✅ 已設定 {emoji} -> {role.name} 的反應身分組", ephemeral=True)

    # 刪除反應身分組
    @app_commands.command(
        name="removereactionrole",
        description="刪除反應身分組（管理員用）"
    )
    @app_commands.describe(
        message="訊息文字或訊息連結",
        emoji="對應的表情符號",
        channel="訊息所在頻道（可不選，用訊息連結即可）"
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

        # 嘗試解析訊息連結
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
                await interaction.response.send_message("❌ 無法解析訊息連結，請確認格式正確", ephemeral=True)
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

        # 移除設定
        guild_roles = self.reaction_roles.get(interaction.guild_id, {})
        msg_roles = guild_roles.get(msg_obj.id, {})
        if emoji in msg_roles:
            del msg_roles[emoji]
            await interaction.response.send_message(f"✅ 已移除 {emoji} 的反應身分組", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 找不到該反應身分組設定", ephemeral=True)

    # 監聽添加反應事件
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

    # 監聽移除反應事件
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
        self.rps_choices = {"剪刀":"✂️", "石頭":"🪨", "布":"📄"}


        self.active_games = {}  # {guild_id: game_info}

    class RPSView(ui.View):
        EMOJIS = {"剪刀":"✂️","石頭":"🪨","布":"📄"}

        def __init__(self, game_cog, rounds, allowed_players, vs_bot):
            super().__init__(timeout=None)
            self.game_cog = game_cog
            self.rounds = rounds
            self.allowed_players = allowed_players  # None = 所有人
            self.vs_bot = vs_bot
            self.players = {}  # {player_id: {"score": 0}}
            self.current_round = 1
            self.choices = {}  # 本局出拳

        async def join_game(self, interaction: Interaction):
            if self.allowed_players and interaction.user.id not in self.allowed_players:
                await interaction.response.send_message("❌ 你沒有被邀請參加這場遊戲！", ephemeral=True)
                return
            if interaction.user.id in self.players:
                await interaction.response.send_message("❌ 你已經加入遊戲了！", ephemeral=True)
                return
            self.players[interaction.user.id] = {"score": 0}
            mention_text = f"（邀請玩家：{interaction.user.mention}）" if self.allowed_players else ""
            await interaction.response.send_message(f"✅ {interaction.user.mention} 已加入遊戲！{mention_text}", ephemeral=True)
            await self.update_status(interaction)

        async def button_callback(self, interaction: Interaction, choice: str):
            if interaction.user.id not in self.players:
                await interaction.response.send_message("❌ 你不是這場遊戲的玩家！", ephemeral=True)
                return

            self.choices[interaction.user.id] = choice
            await self.update_status(interaction)

            # 檢查是否所有玩家已出拳
            if len(self.choices) == len(self.players):
                results_text = ""
                # 計算每個玩家對機器人的勝負
                if self.vs_bot:
                    bot_choice = random.choice(["剪刀","石頭","布"])
                    for pid, data in self.players.items():
                        player_choice = self.choices[pid]
                        if player_choice == bot_choice:
                            result = "平手 🤝"
                        elif (player_choice=="剪刀" and bot_choice=="布") or \
                             (player_choice=="石頭" and bot_choice=="剪刀") or \
                             (player_choice=="布" and bot_choice=="石頭"):
                            result = "你贏了 🎉"
                            data["score"] += 1
                        else:
                            result = "你輸了 😢"
                        results_text += f"<@{pid}> {self.EMOJIS[player_choice]} vs 🤖 {self.EMOJIS[bot_choice]} → {result}\n"
                else:
                    # 多人對戰
                    # 收集出拳並比對所有玩家互相計算勝負
                    choices = {pid:self.choices[pid] for pid in self.players}
                    for pid, data in self.players.items():
                        wins = 0
                        for opp_id, opp_choice in choices.items():
                            if pid == opp_id:
                                continue
                            if (choices[pid]=="剪刀" and opp_choice=="布") or \
                               (choices[pid]=="石頭" and opp_choice=="剪刀") or \
                               (choices[pid]=="布" and opp_choice=="石頭"):
                                wins += 1
                        data["score"] += wins
                        results_text += f"<@{pid}> 出 {self.EMOJIS[choices[pid]]} → 勝 {wins} 場\n"

                self.current_round += 1
                self.choices = {}
                if self.current_round > self.rounds:
                    final_scores = "\n".join([f"<@{pid}> 總分: {data['score']}" for pid, data in self.players.items()])
                    await interaction.message.edit(content=f"🎮 遊戲結束！\n{results_text}\n🏆 最終結果：\n{final_scores}", view=None)
                    self.game_cog.active_games.pop(interaction.guild_id, None)
                else:
                    await interaction.message.edit(content=f"第 {self.current_round} 局開始！選擇你的出拳：", view=self)

        async def update_status(self, interaction: Interaction):
            # 顯示每個玩家出拳狀態
            status_lines = []
            for pid in self.players:
                if pid in self.choices:
                    status_lines.append(f"<@{pid}> 出 {self.EMOJIS[self.choices[pid]]}")
                else:
                    status_lines.append(f"<@{pid}> 出 ❓")
            await interaction.message.edit(content=f"🎮 第 {self.current_round} 局\n" + "\n".join(status_lines), view=self)

    @app_commands.command(name="rps_invite", description="邀請玩家玩剪刀石頭布")
    @app_commands.describe(
        rounds="總局數",
        opponent="指定玩家（可選）",
        vs_bot="是否與機器人 PK"
    )
    async def rps_invite(self, interaction: Interaction, rounds: int = 3, opponent: app_commands.User = None, vs_bot: bool = True):
        if interaction.guild_id in self.active_games:
            await interaction.response.send_message("❌ 本伺服器已有進行中的 RPS 遊戲", ephemeral=True)
            return

        allowed_players = [opponent.id] if opponent else None

        view = self.RPSView(self, rounds, allowed_players, vs_bot)

        # 加入按鈕
        join_button = ui.Button(label="加入遊戲", style=discord.ButtonStyle.success)
        join_button.callback = lambda inter: view.join_game(inter)
        view.add_item(join_button)

        # 出拳按鈕
        for c in ["剪刀","石頭","布"]:
            button = ui.Button(label=c, style=discord.ButtonStyle.primary)
            button.callback = lambda inter, choice=c: view.button_callback(inter, choice)
            view.add_item(button)

        invite_text = f"🎮 {interaction.user.mention} 發起了剪刀石頭布遊戲！總局數：{rounds}\n"
        if opponent:
            invite_text += f"邀請玩家：{opponent.mention}\n"
        else:
            invite_text += "任何人都可以加入\n"
        invite_text += f"是否與機器人 PK: {'是' if vs_bot else '否'}"

        await interaction.response.send_message(invite_text, view=view)
        self.active_games[interaction.guild_id] = {"view": view}


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
    await bot.add_cog(PingCog(bot))
    await bot.add_cog(ReactionRoleCog(bot))
    # 啟動 Bot
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⚡ Bot 已停止")