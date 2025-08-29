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
@tree.command(name="say", description="讓機器人代你說話（只有自己看到）")
@app_commands.describe(message="要說的內容")
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message, ephemeral=True)

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

#-----------------------------
#giveaway 開始抽獎
#-----------------------------
@tree.command(name="giveaway", description="開啟抽獎")
@app_commands.describe(
    prize="獎品名稱",
    duration="持續時間（秒）",
    role="限定參加的角色（可不填）"
)
async def giveaway(interaction: discord.Interaction, prize: str, duration: int, role: discord.Role = None):
    if not is_main_instance():
        await interaction.response.send_message("❌ 目前這個 Bot instance 不負責抽獎", ephemeral=True)
        return
    
    if interaction.channel.id in active_giveaways:
        await interaction.response.send_message("⚠️ 這個頻道已經有正在進行的抽獎！", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎉 抽獎開始！",
        description=f"獎品：**{prize}**\n持續時間：{str(timedelta(seconds=duration))}\n"
                    f"請輸入訊息參加！（{('限定角色：' + role.name) if role else '所有人可參加'})",
        color=discord.Color.green()
    )
    msg = await interaction.response.send_message(embed=embed)
    active_giveaways[interaction.channel.id] = {"prize": prize, "role": role, "active": True}

    # 倒數計時
    for i in range(duration, 0, -10):
        if not active_giveaways.get(interaction.channel.id, {}).get("active"):
            return
        await asyncio.sleep(10)
        new_embed = embed.copy()
        new_embed.description = f"獎品：**{prize}**\n剩餘時間：{str(timedelta(seconds=i))}"
        await (await interaction.original_response()).edit(embed=new_embed)

    # 時間結束 -> 開獎
    await end_giveaway(interaction.channel)

#-----------------------------
#endgiveaway 提前開獎
# -----------------------------
@tree.command(name="endgiveaway", description="提前結束抽獎")
async def endgiveaway(interaction: discord.Interaction):
    if interaction.channel.id not in active_giveaways:
        await interaction.response.send_message("⚠️ 這個頻道沒有進行中的抽獎", ephemeral=True)
        return
    await interaction.response.send_message("✅ 抽獎已提前結束")
    await end_giveaway(interaction.channel)

#-----------------------------
# /cancelgiveaway 取消抽獎
#-----------------------------
@tree.command(name="cancelgiveaway", description="取消進行中的抽獎")
async def cancelgiveaway(interaction: discord.Interaction):
    if interaction.channel.id not in active_giveaways:
        await interaction.response.send_message("⚠️ 這個頻道沒有進行中的抽獎", ephemeral=True)
        return
    active_giveaways[interaction.channel.id]["active"] = False
    del active_giveaways[interaction.channel.id]
    await interaction.response.send_message("❌ 抽獎已取消！")

#-----------------------------
#開獎邏輯
#-----------------------------
async def end_giveaway(channel: discord.TextChannel):
    giveaway = active_giveaways.get(channel.id)
    if not giveaway:
        return
    prize = giveaway["prize"]
    role = giveaway["role"]

    messages = [m async for m in channel.history(limit=200)]
    participants = {m.author for m in messages if not m.author.bot}

    # 限定角色參與
    if role:
        participants = {p for p in participants if role in p.roles}

    if participants:
        winner = random.choice(list(participants))
        await channel.send(f"🎊 恭喜 {winner.mention} 抽中 **{prize}**！")
    else:
        await channel.send("⚠️ 沒有人參加抽獎！")

    del active_giveaways[channel.id]

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