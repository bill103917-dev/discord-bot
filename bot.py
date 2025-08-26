import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Bot is ready! Logged in as {bot.user}')

@bot.event
async def on_error(event, *args, **kwargs):
    print(f'Error on {event}:', args, kwargs)

TOKEN = os.getenv("DISCORD_TOKEN")
print("Token loaded:", TOKEN is not None)
bot.run(TOKEN)