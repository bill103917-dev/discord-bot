import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ 已登入為 {bot.user}")

@bot.command()
async def 哈囉(ctx):
    await ctx.send("嗨！我是你的 Bot 🤖")

bot.run(os.getenv("DISCORD_TOKEN"))
