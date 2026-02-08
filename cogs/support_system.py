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
        embed.add_field(name="您的原始問題:", value=f"```\n{self.original_content[:1000]}\n```", inline=False)
        embed.set_footer(text=f"回覆者：{admin_name} | {safe_now()}")

        if user_obj:
            try:
                await user_obj.send(embed=embed)
                await interaction.followup.send("✅ 回覆已成功發送。", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ 無法私訊用戶。", ephemeral=True)
        else:
            await interaction.followup.send("❌ 找不到該用戶。", ephemeral=True)

class ReplyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label='回覆問題', style=discord.ButtonStyle.success, emoji="💬", custom_id="support_reply_btn")
    async def reply_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 您沒有權限。", ephemeral=True)
        
        try:
            embed = interaction.message.embeds[0]
            user_id = int(embed.footer.text.split("ID: ")[1].split(" |")[0])
            content = embed.description.split("訊息內容:**\n```\n")[1].split("\n```")[0]
            await interaction.response.send_modal(ReplyModal(user_id, content))
        except:
            await interaction.response.send_message("❌ 無法解析訊息。", ephemeral=True)

    @ui.button(label='已處理', style=discord.ButtonStyle.danger, emoji="🛑", custom_id="support_stop_btn")
    async def stop_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 無法操作。", ephemeral=True)
        
        embed = interaction.message.embeds[0]
        embed.title = f"🛑 已處理 - 由 {interaction.user.display_name}"
        embed.color = discord.Color.light_grey()
        await interaction.response.edit_message(embed=embed, view=None)

# =========================
# -- Server Selection
# =========================
class ServerSelectView(ui.View):
    def __init__(self, bot, user_id, cog):
        super().__init__(timeout=60) # 建議私訊選單設定超時
        self.bot = bot
        self.user_id = user_id
        self.cog = cog
        
        # 找出使用者所在的伺服器，且該伺服器有設定支援頻道
        shared_guilds = [
            g for g in self.bot.guilds 
            if g.get_member(self.user_id) is not None and g.id in self.cog.support_config
        ]
        
        if not shared_guilds:
            # 如果沒有共同伺服器或都沒設定，這部分由 on_message 處理，這裡不加 item
            return

        options = [
            discord.SelectOption(label=g.name, value=str(g.id), emoji="🏢") 
            for g in shared_guilds
        ]
        
        select = ui.Select(placeholder="請選擇要聯繫的伺服器...", options=options, custom_id="support_server_select")
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: Interaction):
        selected_id = int(interaction.data['values'][0])
        guild = self.bot.get_guild(selected_id)
        
        self.cog.user_target_guild[self.user_id] = selected_id
        await self.cog.db_save_user_target(self.user_id, selected_id)
        
        await interaction.response.edit_message(
            content=f"✅ 已設定發送目標：**{guild.name}**\n現在您可以直接發送訊息給我，我會幫您轉發！", 
            embed=None, 
            view=None
        )


# =========================
# -- SupportCog Core
# =========================

class SupportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_url = os.getenv("DATABASE_URL")
        self.support_config = {}
        self.user_target_guild = {}
        self.pool = None
        self._cd_mapping = commands.CooldownMapping.from_cooldown(1, 7.0, commands.BucketType.user)

    async def cog_load(self):
        await self.init_db()

    async def init_db(self):
        try:
            self.pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=3)
            async with self.pool.acquire() as conn:
                await conn.execute('CREATE TABLE IF NOT EXISTS support_configs (guild_id BIGINT PRIMARY KEY, channel_id BIGINT, role_id BIGINT)')
                await conn.execute('CREATE TABLE IF NOT EXISTS user_targets (user_id BIGINT PRIMARY KEY, guild_id BIGINT)')
                
                for r in await conn.fetch('SELECT * FROM support_configs'):
                    self.support_config[r['guild_id']] = (r['channel_id'], r['role_id'])
                for t in await conn.fetch('SELECT * FROM user_targets'):
                    self.user_target_guild[t['user_id']] = t['guild_id']
            print("✅ SupportCog: Database Pool Ready.")
        except Exception as e:
            print(f"❌ DB Error: {e}")

    async def db_save_config(self, g_id, c_id, r_id):
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO support_configs VALUES ($1,$2,$3) ON CONFLICT (guild_id) DO UPDATE SET channel_id=$2, role_id=$3', g_id, c_id, r_id)

    async def db_save_user_target(self, u_id, g_id):
        async with self.pool.acquire() as conn:
            if g_id is None: await conn.execute('DELETE FROM user_targets WHERE user_id=$1', u_id)
            else: await conn.execute('INSERT INTO user_targets VALUES ($1,$2) ON CONFLICT (user_id) DO UPDATE SET guild_id=$2', u_id, g_id)

    @app_commands.command(name="set_support_channel", description="設定轉發頻道")
    @app_commands.default_permissions(manage_guild=True)
    async def set_support_channel(self, interaction: Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        g_id, c_id, r_id = interaction.guild.id, channel.id, (role.id if role else None)
        self.support_config[g_id] = (c_id, r_id)
        await self.db_save_config(g_id, c_id, r_id)
        await interaction.response.send_message(f"✅ 設定成功，轉發至 {channel.mention}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None: return
        
        retry_after = self._cd_mapping.get_bucket(message).update_rate_limit()
        if retry_after: return 

        u_id = message.author.id
        target_id = self.user_target_guild.get(u_id)

        # 檢查是否有目標伺服器且該伺服器配置還在
        if target_id and target_id in self.support_config:
            await self.process_forward(message.author, message.content, target_id)
        else:
            # 建立 View
            view = ServerSelectView(self.bot, u_id, self)
            
            # 檢查 View 裡面有沒有選單（透過檢查 children 數量）
            if len(view.children) == 0:
                return await message.channel.send(
                    "❌ 找不到可用的伺服器。請確保您與機器人在同一個伺服器，且該伺服器已設定支援頻道。"
                )

            embed = discord.Embed(
                title="📞 聯繫管理員", 
                description="偵測到您想發送問題，但尚未設定目標伺服器。\n請從下方選單選擇一個伺服器：", 
                color=0x3498db
            )
            await message.channel.send(embed=embed, view=view)

    async def process_forward(self, user, question, guild_id):
        guild = self.bot.get_guild(guild_id)
        config = self.support_config.get(guild_id)
        if not guild or not config or not (channel := guild.get_channel(config[0])): return

        embed = discord.Embed(title=f"❓ 來自 {user.name}", description=f"**訊息內容:**\n```\n{question[:1500]}\n```", color=0xf1c40f)
        embed.set_footer(text=f"User ID: {user.id} | {safe_now()}")
        
        view = ReplyView()
        if match := re.search(r"(https?://[^\s]+)", question):
            view.add_item(ui.Button(label="🔗 連結", url=match.group(0)))

        mention = f"<@&{config[1]}>" if config[1] else "@here"
        await channel.send(content=mention, embed=embed, view=view)
        await user.send(f"✅ 已送達 **{guild.name}**。")

    async def cog_unload(self):
        if self.pool: await self.pool.close()

async def setup(bot):
    await bot.add_cog(SupportCog(bot))
