import os
import discord
from discord.ext import commands
from discord import app_commands
import nest_asyncio

# 避免 Colab / Railway 出現事件循環衝突
nest_asyncio.apply()

# 設定 Intents
intents = discord.Intents.default()
intents.message_content = True

# 建立 Bot
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------- 事件 --------------------
@bot.event
async def on_ready():
    print(f"✅ Bot 已上線: {bot.user}")
    try:
        synced = await bot.tree.sync()  # 同步 Slash 指令
        print(f"📌 已同步 {len(synced)} 個斜線指令 (/)")
    except Exception as e:
        print(f"❌ 同步失敗: {e}")

# -------------------- Slash 指令 --------------------

# /哈囉
@bot.tree.command(name="哈囉", description="跟機器人打招呼")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"嗨 {interaction.user.mention} 👋 我是你的 Bot！")

# /加法
@bot.tree.command(name="加法", description="計算兩個數字相加")
async def add(interaction: discord.Interaction, x: int, y: int):
    await interaction.response.send_message(f"{x} + {y} = {x + y}")

# /重啟
@bot.tree.command(name="重啟", description="重新啟動機器人")
async def restart(interaction: discord.Interaction):
    await interaction.response.send_message("♻️ 正在重新啟動 Bot...")
    await bot.close()

# /說
@bot.tree.command(name="說", description="讓 Bot 幫你說話")
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message)

# /ping
@bot.tree.command(name="ping", description="查看延遲 (ms)")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! 延遲 {latency}ms")

# /roll
@bot.tree.command(name="roll", description="擲骰子 🎲")
async def roll(interaction: discord.Interaction, sides: int = 6):
    import random
    result = random.randint(1, sides)
    await interaction.response.send_message(f"🎲 你擲出了 {result} (1-{sides})")

# -------------------- 啟動 Bot --------------------
bot.run(os.getenv("DISCORD_TOKEN"))