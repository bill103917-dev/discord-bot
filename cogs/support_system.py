import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
import asyncpg
import os
import asyncio
import re
from typing import Dict, Tuple, Optional
from datetime import datetime

# =========================
# -- 工具與基礎設定
# =========================
# 匯入時間工具
from utils.time_utils import safe_now



# =========================
# -- Views & Modal (Support)
# =========================

class ReplyModal(ui.Modal, title='回覆用戶問題'):
    response_title = ui.TextInput(label='回覆標題 (可選)', required=False, max_length=100)
    response_content = ui.TextInput(label='回覆內容', style=discord.TextStyle.long, required=True, max_length=1500)

    def __init__(self, original_user_id: int, original_content: str):
        super().__init__()
        self.original_user_id = original_user_id
        self.original_content = original_content

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        user_obj = interaction.client.get_user(self.original_user_id)
        admin_name = interaction.user.display_name
        reply_content = str(self.response_content).strip()
        response_title = str(self.response_title).strip() or "管理員回覆"

        embed = discord.Embed(
            title=f"💬 {response_title}",
            description=f"**管理員說：**\n>>> {reply_content}",
            color=discord.Color.green()
        )
        embed.add_field(
            name="管理員回覆您的問題:", 
            value=f"```\n{self.original_content[:1000]}\n```", 
            inline=False
        )
        embed.set_footer(text=f"回覆者：{admin_name} | {safe_now()}")

        if user_obj:
            try:
                await user_obj.send(embed=embed)
                await interaction.followup.send("✅ 回覆已成功發送。", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ 無法私訊用戶（可能被封鎖）。", ephemeral=True)
        else:
            await interaction.followup.send("❌ 找不到該用戶。", ephemeral=True)

class ReplyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label='回覆問題', style=discord.ButtonStyle.success, emoji="💬", custom_id="support_reply_btn")
    async def reply_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 您沒有權限回覆此問題。", ephemeral=True)
        
        try:
            embed = interaction.message.embeds[0]
            # 從 Footer 提取 ID: "User ID: 123456789 | ..."
            user_id = int(embed.footer.text.split("ID: ")[1].split(" |")[0])
            # 提取原始問題內容
            content = embed.description.split("訊息內容:**\n```\n")[1].split("\n```")[0]
        except:
            return await interaction.response.send_message("❌ 無法解析訊息內容，請手動私訊用戶。", ephemeral=True)

        await interaction.response.send_modal(ReplyModal(user_id, content))

    @ui.button(label='已處理', style=discord.ButtonStyle.danger, emoji="🛑", custom_id="support_stop_btn")
    async def stop_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 無法操作。", ephemeral=True)
        
        embed = interaction.message.embeds[0]
        embed.title = f"🛑 已處理 - 由 {interaction.user.display_name}"
        embed.color = discord.Color.light_grey()
        
        view = ui.View(timeout=None)
        view.add_item(ui.Button(label=f'處理完畢 ({interaction.user.display_name})', disabled=True))
        await interaction.response.edit_message(embed=embed, view=view)

# =========================
# -- Server Selection View
# =========================

class ServerSelectView(ui.View):
    def __init__(self, bot: commands.Bot, user_id: int, cog):
        super().__init__(timeout=None)
        self.bot = bot
        self.user_id = user_id
        self.cog = cog
        
        # 建立選單
        self.server_select = ui.Select(placeholder="請選擇伺服器...", custom_id=f"support_select_{user_id}")
        self.server_select.callback = self._on_select
        self.add_item(self.server_select)
        
        self.reset_button = ui.Button(label="重新選擇", style=discord.ButtonStyle.secondary, custom_id=f"support_reset_{user_id}", disabled=True)
        self.reset_button.callback = self._on_reset
        self.add_item(self.reset_button)
        
        self._load_options()

    def _load_options(self):
        shared_guilds = [g for g in self.bot.guilds if g.get_member(self.user_id) is not None]
        options = []
        for guild in shared_guilds:
            if guild.id in self.cog.support_config:
                desc = "✅ 管理員已設定接收頻道"
            else:
                desc = "⚠️ 該伺服器尚未設定支援功能"
            options.append(discord.SelectOption(label=guild.name, value=str(guild.id), description=desc))
        
        if not options:
            self.server_select.disabled = True
            self.server_select.placeholder = "無共享伺服器"
        else:
            self.server_select.options = options

    async def _on_select(self, interaction: Interaction):
        selected_id = int(self.server_select.values[0])
        if selected_id not in self.cog.support_config:
            return await interaction.response.send_message("❌ 該伺服器管理員尚未設定此功能。", ephemeral=True)
        
        self.cog.user_target_guild[self.user_id] = selected_id
        await self.cog.db_save_user_target(self.user_id, selected_id)
        
        await interaction.response.edit_message(
            embed=discord.Embed(title="✅ 設定成功", description=f"您現在發送的訊息將轉發至：**{self.bot.get_guild(selected_id).name}**", color=discord.Color.green()),
            view=None # 選擇後移除選單，或更新狀態
        )

    async def _on_reset(self, interaction: Interaction):
        self.cog.user_target_guild.pop(self.user_id, None)
        await self.cog.db_save_user_target(self.user_id, None)
        await interaction.response.send_message("🔄 已重置選擇，請重新發送訊息選擇伺服器。", ephemeral=True)

# =========================
# -- SupportCog Core
# =========================

class SupportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_url = os.getenv("DATABASE_URL")
        self.support_config = {}
        self.user_target_guild = {}

     async def cog_load(self):
         """當 Cog 被載入時自動執行"""
         asyncio.create_task(self.init_db())
         print("✅ SupportCog: 已在背景啟動資料庫初始化任務")



    async def init_db(self):
        try:
            conn = await asyncpg.connect(self.db_url)
            await conn.execute('CREATE TABLE IF NOT EXISTS support_configs (guild_id BIGINT PRIMARY KEY, channel_id BIGINT, role_id BIGINT)')
            await conn.execute('CREATE TABLE IF NOT EXISTS user_targets (user_id BIGINT PRIMARY KEY, guild_id BIGINT)')
            
            rows = await conn.fetch('SELECT * FROM support_configs')
            for r in rows: self.support_config[r['guild_id']] = (r['channel_id'], r['role_id'])
            
            targets = await conn.fetch('SELECT * FROM user_targets')
            for t in targets: self.user_target_guild[t['user_id']] = t['guild_id']
            
            await conn.close()
            print("✅ Support System Database Connected & Synced.")
        except Exception as e:
            print(f"❌ DB Error: {e}")

    async def db_save_config(self, g_id, c_id, r_id):
        conn = await asyncpg.connect(self.db_url)
        await conn.execute('INSERT INTO support_configs VALUES ($1,$2,$3) ON CONFLICT (guild_id) DO UPDATE SET channel_id=$2, role_id=$3', g_id, c_id, r_id)
        await conn.close()

    async def db_save_user_target(self, u_id, g_id):
        conn = await asyncpg.connect(self.db_url)
        if g_id is None: await conn.execute('DELETE FROM user_targets WHERE user_id=$1', u_id)
        else: await conn.execute('INSERT INTO user_targets VALUES ($1,$2) ON CONFLICT (user_id) DO UPDATE SET guild_id=$2', u_id, g_id)
        await conn.close()

    @app_commands.command(name="set_support_channel")
    @app_commands.default_permissions(manage_guild=True)
    async def set_support_channel(self, interaction: Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        await interaction.response.defer(ephemeral=True)
        g_id, c_id, r_id = interaction.guild.id, channel.id, (role.id if role else None)
        self.support_config[g_id] = (c_id, r_id)
        await self.db_save_config(g_id, c_id, r_id)
        await interaction.followup.send(f"✅ 已設定轉發至 {channel.mention}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return
        
        user_id = message.author.id
        target_id = self.user_target_guild.get(user_id)

        if target_id and target_id in self.support_config:
            await self.process_forward(message.author, message.content, target_id)
        else:
            view = ServerSelectView(self.bot, user_id, self)
            await message.channel.send(embed=discord.Embed(title="📞 聯繫管理員", description="請選擇您要發送問題的伺服器：", color=discord.Color.blue()), view=view)

    async def process_forward(self, user: discord.User, question: str, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        config = self.support_config.get(guild_id)
        if not guild or not config: return

        channel = guild.get_channel(config[0])
        if not channel: return

        embed = discord.Embed(
            title=f"❓ 來自 {user.name} 的問題",
            description=f"**發送者:** <@{user.id}>\n**伺服器:** `{guild.name}`\n\n**訊息內容:**\n```\n{question}\n```",
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"User ID: {user.id} | 時間: {safe_now()}")

        view = ReplyView()
        match = re.search(r"(https?://[^\s]+)", question)
        if match:
            view.add_item(ui.Button(label="🔗 開啟連結", url=match.group(0)))

        mention = f"<@&{config[1]}>" if config[1] else "@here"
        await channel.send(content=mention, embed=embed, view=view)
        await user.send(f"✅ 訊息已送達 **{guild.name}**。")

async def setup(bot):
    await bot.add_cog(SupportCog(bot))
