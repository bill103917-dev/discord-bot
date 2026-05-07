import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
import asyncpg
import os
import asyncio
import re
from typing import Dict, Tuple, Optional
from datetime import datetime
import aiohttp

# =========================
# -- 工具與基礎設定
# =========================
from utils.time_utils import safe_now

# =========================
# -- 1. 回覆彈窗 (Modal)
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
            except:
                await interaction.followup.send("❌ 無法私訊用戶。", ephemeral=True)

# =========================
# -- 2. 伺服器選擇選單 (Server Select)
# =========================
class ServerSelectView(ui.View):
    def __init__(self, bot, user_id, cog):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.cog = cog
        
        shared = [g for g in bot.guilds if g.get_member(user_id) and g.id in cog.support_config]
        if shared:
            options = [discord.SelectOption(label=g.name, value=str(g.id), emoji="🏢") for g in shared]
            select = ui.Select(placeholder="請選擇目標伺服器...", options=options)
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: Interaction):
        if self.cog.pool is None:
            return await interaction.response.send_message("❌ 資料庫未連線，請稍後再試。", ephemeral=True)

        sid = int(interaction.data['values'][0])
        guild_name = self.bot.get_guild(sid).name
        
        self.cog.user_target_guild[self.user_id] = sid
        async with self.cog.pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO user_targets (user_id, guild_id) VALUES ($1, $2) '
                'ON CONFLICT (user_id) DO UPDATE SET guild_id = $2', 
                self.user_id, sid
            )
        await interaction.response.edit_message(content=f"✅ 已成功將目標設定為：**{guild_name}**\n現在您可以直接發送私訊，我會幫您轉發。", view=None)

# =========================
# -- 3. 臨時聊天室控制 (Control View)
# =========================
class TempChatControlView(ui.View):
    def __init__(self, cog, user_id, origin_msg=None):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id
        self.origin_msg = origin_msg
        self.confirm_state = False

    @ui.button(label='結束此對話', style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="end_chat_btn")
    async def end_chat(self, interaction: Interaction, button: ui.Button):
        if not self.confirm_state:
            self.confirm_state = True
            button.label = "確認結束並結案？"
            button.style = discord.ButtonStyle.danger
            await interaction.response.edit_message(view=self)
            await asyncio.sleep(5)
            if self.confirm_state:
                self.confirm_state = False
                button.label = "結束此對話"
                button.style = discord.ButtonStyle.secondary
                try: await interaction.edit_original_response(view=self)
                except: pass
        else:
            await interaction.response.send_message("📂 正在產生紀錄並執行自動結案程序...")
            await self.cog.execute_final_close(
                origin_msg=self.origin_msg, 
                user_id=str(self.user_id), 
                channel=interaction.channel,
                closer_name=interaction.user.display_name
            )

# =========================
# -- 4. 管理端主按鈕 (Reply View)
# =========================
class ReplyView(ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(label='回覆問題', style=discord.ButtonStyle.success, emoji="💬", custom_id="support_reply_btn")
    async def reply_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 無權限。", ephemeral=True)
        try:
            embed = interaction.message.embeds[0]
            user_id = int(re.search(r"ID: (\d+)", embed.footer.text).group(1))
            content_match = re.search(r"```\n?(.*?)\n?```", embed.description, re.DOTALL)
            content = content_match.group(1) if content_match else "無法解析內容"
            await interaction.response.send_modal(ReplyModal(user_id, content))
        except:
            await interaction.response.send_message("❌ 解析失敗。", ephemeral=True)

    @ui.button(label='發起臨時聊天', style=discord.ButtonStyle.primary, emoji="🚀", custom_id="support_chat_invite_btn")
    async def chat_invite_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 無權限。", ephemeral=True)
        try:
            await interaction.response.defer(ephemeral=True)
            embed = interaction.message.embeds[0]
            user_id = int(re.search(r"ID: (\d+)", embed.footer.text).group(1))
            user_obj = interaction.client.get_user(user_id) or await interaction.client.fetch_user(user_id)
            
            from cogs.support_system import ChatInviteView # 避免循環引用
            invite_view = ChatInviteView(interaction.user, user_obj, self.cog, origin_msg=interaction.message)
            await user_obj.send(f"🔔 **來自 {interaction.guild.name} 管理員的邀請**", view=invite_view)
            await interaction.followup.send(f"✅ 已對 **{user_obj.name}** 發起邀請。", ephemeral=True)
        except:
            await interaction.followup.send("❌ 邀請失敗。", ephemeral=True)

    @ui.button(label='已處理', style=discord.ButtonStyle.danger, emoji="🛑", custom_id="support_stop_btn")
    async def stop_button(self, interaction: Interaction, button: ui.Button):
        await self.cog.reply_view_stop_callback(interaction)

# =========================
# -- 5. 邀請視圖 (Chat Invite)
# =========================
class ChatInviteView(ui.View):
    def __init__(self, sender, receiver, cog, origin_msg=None):
        super().__init__(timeout=120)
        self.sender = sender
        self.receiver = receiver
        self.cog = cog
        self.origin_msg = origin_msg 

    @ui.button(label='接受邀請', style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.receiver.id:
            return await interaction.response.send_message("這不是給你的邀請。", ephemeral=True)
        
        await interaction.response.defer()
        target_id = self.cog.user_target_guild.get(self.receiver.id)
        guild = interaction.client.get_guild(target_id)
        if not guild: return await interaction.followup.send("❌ 找不到伺服器。", ephemeral=True)

        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                self.sender: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
                self.receiver: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)
            }
            channel = await guild.create_text_channel(
                name=f"chat-{self.receiver.name}",
                overwrites=overwrites,
                topic=f"User ID: {self.receiver.id}"
            )

            go_view = ui.View()
            go_view.add_item(ui.Button(label="前往聊天室", url=channel.jump_url, emoji="🔗"))
            
            if self.origin_msg:
                admin_new_view = ReplyView(self.cog)
                admin_new_view.clear_items()
                admin_new_view.add_item(ui.Button(label="已開啟對話室", url=channel.jump_url, style=discord.ButtonStyle.link, emoji="💬"))
                stop_btn = ui.Button(label='已處理', style=discord.ButtonStyle.danger, emoji="🛑", custom_id="support_stop_btn")
                stop_btn.callback = lambda i: self.cog.reply_view_stop_callback(i)
                admin_new_view.add_item(stop_btn)
                await self.origin_msg.edit(view=admin_new_view)

            await interaction.followup.send(f"✅ 頻道已建立：{channel.mention}", view=go_view, ephemeral=True)
            await self.sender.send(f"✅ {self.receiver.name} 已接受邀請！", view=go_view)
            await channel.send(
                f"✨ {self.sender.mention} & {self.receiver.mention} 已連線。", 
                view=TempChatControlView(self.cog, self.receiver.id, self.origin_msg)
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 建立失敗: {e}", ephemeral=True)

# =========================
# -- 6. 核心 Cog
# =========================
class SupportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_url = os.getenv("DATABASE_URL")
        self.support_config = {}
        self.user_target_guild = {}
        self.pool = None
        self.transcript_dir = "transcripts"
        self._cd_mapping = commands.CooldownMapping.from_cooldown(1, 7.0, commands.BucketType.user)

    async def cog_load(self):
        if not os.path.exists(self.transcript_dir):
            os.makedirs(self.transcript_dir)
        await self.init_db()

    async def init_db(self):
        if not self.db_url: return
        try:
            adj_url = self.db_url.replace("postgres://", "postgresql://", 1)
            self.pool = await asyncpg.create_pool(adj_url, min_size=1, max_size=3, ssl='require')
            async with self.pool.acquire() as conn:
                await conn.execute('CREATE TABLE IF NOT EXISTS support_configs (guild_id BIGINT PRIMARY KEY, channel_id BIGINT, role_id BIGINT)')
                await conn.execute('CREATE TABLE IF NOT EXISTS user_targets (user_id BIGINT PRIMARY KEY, guild_id BIGINT)')
                rows = await conn.fetch('SELECT * FROM support_configs')
                for r in rows: self.support_config[r['guild_id']] = (r['channel_id'], r['role_id'])
                t_rows = await conn.fetch('SELECT * FROM user_targets')
                for tr in t_rows: self.user_target_guild[tr['user_id']] = tr['guild_id']
            print("✅ 資料庫初始化成功")
        except Exception as e: print(f"❌ 資料庫初始化失敗：{e}")

    @app_commands.command(name="set_support_channel", description="設定轉發頻道")
    @app_commands.default_permissions(manage_guild=True)
    async def set_support_channel(self, interaction: Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        await interaction.response.defer(ephemeral=True)
        if self.pool is None: await self.init_db()
        if self.pool is None: return await interaction.followup.send("❌ 資料庫連線失敗。", ephemeral=True)

        gid, cid, rid = interaction.guild.id, channel.id, (role.id if role else None)
        self.support_config[gid] = (cid, rid)
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO support_configs VALUES ($1,$2,$3) ON CONFLICT (guild_id) DO UPDATE SET channel_id=$2, role_id=$3', gid, cid, rid)
        await interaction.followup.send(f"✅ 已設定 {channel.mention} 為轉發頻道", ephemeral=True)

    @app_commands.command(name="select_server", description="選擇或切換您要發送問題的目標伺服器")
    async def select_server(self, interaction: Interaction):
        if interaction.guild is not None:
            return await interaction.response.send_message("❌ 請在私訊中使用此指令。", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        view = ServerSelectView(self.bot, interaction.user.id, self)
        if not view.children:
            return await interaction.followup.send("❌ 找不到可用的伺服器。", ephemeral=True)
        await interaction.followup.send("📞 請選擇目標伺服器：", view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild: return
        if self._cd_mapping.get_bucket(message).update_rate_limit(): return
        
        uid = message.author.id
        tid = self.user_target_guild.get(uid)
        
        if tid in self.support_config:
            guild = self.bot.get_guild(tid)
            config = self.support_config.get(tid)
            if not (guild and (chan := guild.get_channel(config[0]))): return
            
            url_match = re.search(r'https?://[^\s]+', message.content)
            embed = discord.Embed(title=f"❓ 來自 {message.author.name}", description=f"```\n{message.content[:1500]}\n```", color=0xf1c40f)
            embed.set_footer(text=f"User ID: {uid} | {safe_now()}")
            
            view = ReplyView(self)
            if url_match:
                view.add_item(ui.Button(label="開啟連結", url=url_match.group(0), emoji="🔗"))
            
            await chan.send(content=f"<@&{config[1]}>" if config[1] else "@here", embed=embed, view=view)
            await message.author.send(f"✅ 訊息已送達 **{guild.name}**。")
        else:
            view = ServerSelectView(self.bot, uid, self)
            if view.children: await message.channel.send("📞 請選擇伺服器：", view=view)

    async def reply_view_stop_callback(self, interaction: Interaction):
        try: await interaction.response.defer()
        except: return
        old_embed = interaction.message.embeds[0]
        user_id_str = re.search(r"ID: (\d+)", old_embed.footer.text).group(1)
        target_channel = discord.utils.get(interaction.guild.text_channels, topic=f"User ID: {user_id_str}")
        if target_channel:
            confirm_view = ui.View(timeout=60)
            confirm_btn = ui.Button(label="確認結束並結案", style=discord.ButtonStyle.danger)
            confirm_btn.callback = lambda i: self.execute_final_close(interaction.message, user_id_str, target_channel, interaction.user.display_name)
            confirm_view.add_item(confirm_btn)
            await target_channel.send(f"⚠️ {interaction.user.mention} 要求結案。", view=confirm_view)
        else:
            await self.execute_final_close(interaction.message, user_id_str, closer_name=interaction.user.display_name)

    async def execute_final_close(self, origin_msg, user_id, channel=None, closer_name="系統"):
        try:
            file_path = os.path.join(self.transcript_dir, f"transcript_{user_id}.txt")
            if channel:
                msgs = [f"[{m.created_at}] {m.author}: {m.content}" async for m in channel.history(limit=1000, oldest_first=True)]
                with open(file_path, "w", encoding="utf-8") as f: f.write("\n".join(msgs))
                await channel.delete()
            
            old_embed = origin_msg.embeds[0]
            summary_embed = discord.Embed(title="✅ 案件已處理", color=discord.Color.dark_gray())
            summary_embed.description = f"處理人：{closer_name}\n時間：{safe_now()}"
            summary_embed.set_footer(text=f"ID: {user_id}")
            
            view = ui.View()
            if os.path.exists(file_path):
                log_chan = self.bot.get_channel(1470291339118641253) # 請改 ID
                if log_chan:
                    f_msg = await log_chan.send(file=discord.File(file_path))
                    view.add_item(ui.Button(label="查看紀錄", url=f_msg.attachments[0].url))
            await origin_msg.edit(embed=summary_embed, view=view)
        except Exception as e: print(f"❌ 結案出錯: {e}")

async def setup(bot): 
    await bot.add_cog(SupportCog(bot))
