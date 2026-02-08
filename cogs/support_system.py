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
            except discord.Forbidden:
                await interaction.followup.send("❌ 無法私訊用戶。", ephemeral=True)
        else:
            await interaction.followup.send("❌ 找不到該用戶。", ephemeral=True)

# =========================
# -- 2. 臨時聊天室邀請 (Invite View)
# =========================
class ChatInviteView(ui.View):
    def __init__(self, sender, receiver, cog):
        super().__init__(timeout=60)
        self.sender = sender     
        self.receiver = receiver 
        self.cog = cog

    @ui.button(label='接受邀請', style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.receiver.id:
            return await interaction.response.send_message("這不是給你的邀請。", ephemeral=True)
        
        await interaction.response.defer()
        
        # --- 修正核心：定位伺服器 ---
        # 優先從 cog 紀錄中取得目標伺服器，若無則嘗試從管理員所在的伺服器找
        target_guild_id = self.cog.user_target_guild.get(self.receiver.id)
        guild = interaction.client.get_guild(target_guild_id)

        if not guild:
            # 如果找不到紀錄，嘗試找發起人與機器人的共同伺服器
            if hasattr(self.sender, 'guild'):
                guild = self.sender.guild
            else:
                # 最後手段：找機器人所在的伺服器中，該用戶也在裡面的
                guild = next((g for g in interaction.client.guilds if g.get_member(self.receiver.id)), None)

        if not guild:
            return await interaction.followup.send("❌ 找不到建立頻道的目標伺服器，請聯繫管理員。", ephemeral=True)

        # 檢查權限
        if not guild.me.guild_permissions.manage_channels:
            return await interaction.followup.send("❌ 機器人在該伺服器缺少「管理頻道」權限。", ephemeral=True)

        # 建立頻道與權限設定
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.sender: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            self.receiver: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)
        }
        
        try:
            channel = await guild.create_text_channel(
                name=f"chat-{self.receiver.name}",
                overwrites=overwrites,
                topic=f"User ID: {self.receiver.id}"
            )

            view = ui.View()
            view.add_item(ui.Button(label="前往聊天室", url=channel.jump_url))
            
            await interaction.followup.send(f"✅ 您已同意，請前往聊天室。", view=view, ephemeral=True)
            await self.sender.send(f"✅ {self.receiver.name} 已同意邀請！", view=view)
            
            await channel.send(
                f"✨ {self.sender.mention} & {self.receiver.mention} 已連線。\n點擊下方按鈕可結束對話並產生紀錄。", 
                view=TempChatControlView(self.cog, self.receiver.id)
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 建立頻道時發生錯誤: {e}", ephemeral=True)


    @ui.button(label='拒絕', style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: Interaction, button: ui.Button):
        await interaction.response.edit_message(content="❌ 您已拒絕邀請。", view=None)
        await self.sender.send(f"❌ {self.receiver.name} 拒絕了您的聊天邀請。")

# =========================
# -- 3. 聊天室控制與產檔 (Control View)
# =========================
class TempChatControlView(ui.View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id
        self.confirm_state = False

    @ui.button(label='結束此對話', style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="end_chat_btn")
    async def end_chat(self, interaction: Interaction, button: ui.Button):
        if not self.confirm_state:
            self.confirm_state = True
            button.label = "確認結束？ (10秒內再次點擊)"
            button.style = discord.ButtonStyle.danger
            await interaction.response.edit_message(view=self)
            
            await asyncio.sleep(10)
            if self.confirm_state:
                self.confirm_state = False
                button.label = "結束此對話"
                button.style = discord.ButtonStyle.secondary
                try: await interaction.edit_original_response(view=self)
                except: pass
        else:
            await interaction.response.send_message("📂 正在產生紀錄並關閉頻道...")
            await self.close_and_transcript(interaction.channel, self.user_id)

    async def close_and_transcript(self, channel, user_id):
        messages = []
        async for msg in channel.history(limit=1000, oldest_first=True):
            if msg.author.bot and not msg.content.startswith("✨"): continue
            time = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            messages.append(f"[{time}] {msg.author.display_name}: {msg.content}")

        file_path = f"transcript_{user_id}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"--- Chat Log (User ID: {user_id}) ---\n" + "\n".join(messages))
        
        await channel.delete()

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
            return await interaction.response.send_message("❌ 您沒有權限。", ephemeral=True)
        
        try:
            embed = interaction.message.embeds[0]
            # 使用正則解析 ID 與內容
            user_id = int(re.search(r"ID: (\d+)", embed.footer.text).group(1))
            content_match = re.search(r"```\n?(.*?)\n?```", embed.description, re.DOTALL)
            content = content_match.group(1) if content_match else "無法解析內容"
            
            await interaction.response.send_modal(ReplyModal(user_id, content))
        except Exception as e:
            await interaction.response.send_message(f"❌ 解析失敗: {e}", ephemeral=True)

    @ui.button(label='發起臨時聊天', style=discord.ButtonStyle.primary, emoji="🚀", custom_id="support_chat_invite_btn")
    async def chat_invite_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 您沒有權限。", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        try:
            embed = interaction.message.embeds[0]
            user_id = int(re.search(r"ID: (\d+)", embed.footer.text).group(1))
            user_obj = interaction.client.get_user(user_id)
            
            if not user_obj:
                return await interaction.followup.send("❌ 找不到該用戶。", ephemeral=True)

            invite_view = ChatInviteView(sender=interaction.user, receiver=user_obj, cog=self.cog)
            await user_obj.send(
                f"🔔 **來自 {interaction.guild.name} 管理員的邀請**\n管理員 {interaction.user.display_name} 想與您進行對話，是否接受？",
                view=invite_view
            )
            await interaction.followup.send(f"✅ 已對 **{user_obj.name}** 發送聊天邀請。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 邀請失敗: {e}", ephemeral=True)

    @ui.button(label='已處理', style=discord.ButtonStyle.danger, emoji="🛑", custom_id="support_stop_btn")
    async def stop_button(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        
        # --- 數據解析 ---
        old_embed = interaction.message.embeds[0]
        user_id = re.search(r"ID: (\d+)", old_embed.footer.text).group(1)
        user_name = old_embed.title.replace("❓ 來自 ", "")
        content = re.search(r"```\n?(.*?)\n?```", old_embed.description, re.DOTALL).group(1)
        send_time = re.search(r"\| ([\d\-\s:]+)", old_embed.footer.text).group(1)

        # --- 建立你圖片中的總結 Embed ---
        summary_embed = discord.Embed(title="✅ 案件已處理", color=discord.Color.dark_gray())
        summary_embed.description = (
            f"處理人員：{interaction.user.mention}\n"
            f"處理時間：{safe_now()}\n\n"
            f"👤 **用戶資訊**\n名稱：{user_name}\nID：{user_id}\n"
            f"🏢 **伺服器資訊**\n目標：{interaction.guild.name}\nID：{interaction.guild.id}\n"
            f"📊 **統計**\n發送時間：{send_time}\n處理狀態：已結案\n"
            f"📝 **原始問題**\n```\n{content[:500]}\n```"
        )
        summary_embed.set_footer(text=f"處理者：{interaction.user.display_name} | 結案編號：{interaction.message.id}")

        new_view = ui.View(timeout=None)
        jump_url = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{interaction.message.id}"
        new_view.add_item(ui.Button(label="查看訊息紀錄", style=discord.ButtonStyle.link, url=jump_url))

        # 檔案上傳
        file_path = f"transcript_{user_id}.txt"
        if os.path.exists(file_path):
            log_chan = interaction.client.get_channel(123456789) # 📌 這裡填入你的 Log 頻道 ID
            if log_chan:
                file_msg = await log_chan.send(content=f"📁 Log: `{user_id}`", file=discord.File(file_path))
                new_view.add_item(ui.Button(label="查看對話文件", style=discord.ButtonStyle.link, url=file_msg.attachments[0].url))
                os.remove(file_path)

        await interaction.edit_original_response(embed=summary_embed, view=new_view)
        
# =========================
# -- 5. 伺服器選擇 (Server Selection)
# =========================
class ServerSelectView(ui.View):
    def __init__(self, bot, user_id, cog):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.cog = cog
        
        shared_guilds = [g for g in self.bot.guilds if g.get_member(self.user_id) and g.id in self.cog.support_config]
        if shared_guilds:
            options = [discord.SelectOption(label=g.name, value=str(g.id), emoji="🏢") for g in shared_guilds]
            select = ui.Select(placeholder="請選擇伺服器...", options=options)
            select.callback = self._on_select
            self.add_item(select)

    async def _on_select(self, interaction: Interaction):
        sid = int(interaction.data['values'][0])
        self.cog.user_target_guild[self.user_id] = sid
        await self.cog.db_save_user_target(self.user_id, sid)
        await interaction.response.edit_message(content=f"✅ 已設定發送目標：**{self.bot.get_guild(sid).name}**", view=None)

# =========================
# -- 6. SupportCog Core
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
                await conn.execute('CREATE TABLE IF NOT EXISTS temp_chats (channel_id BIGINT PRIMARY KEY, user_id BIGINT, admin_id BIGINT, created_at TIMESTAMP)')
                
                rows = await conn.fetch('SELECT * FROM support_configs')
                for r in rows: self.support_config[r['guild_id']] = (r['channel_id'], r['role_id'])
                t_rows = await conn.fetch('SELECT * FROM user_targets')
                for tr in t_rows: self.user_target_guild[tr['user_id']] = tr['guild_id']
            print("✅ SupportCog Database Ready.")
        except Exception as e: print(f"❌ DB Error: {e}")

    async def db_save_config(self, g, c, r):
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO support_configs VALUES ($1,$2,$3) ON CONFLICT (guild_id) DO UPDATE SET channel_id=$2, role_id=$3', g, c, r)

    async def db_save_user_target(self, u, g):
        async with self.pool.acquire() as conn:
            if g is None: await conn.execute('DELETE FROM user_targets WHERE user_id=$1', u)
            else: await conn.execute('INSERT INTO user_targets VALUES ($1,$2) ON CONFLICT (user_id) DO UPDATE SET guild_id=$2', u, g)

    @app_commands.command(name="set_support_channel", description="設定轉發頻道")
    @app_commands.default_permissions(manage_guild=True)
    async def set_support_channel(self, interaction: Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        gid, cid, rid = interaction.guild.id, channel.id, (role.id if role else None)
        self.support_config[gid] = (cid, rid)
        await self.db_save_config(gid, cid, rid)
        await interaction.response.send_message(f"✅ 已設定至 {channel.mention}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild: return
        if self._cd_mapping.get_bucket(message).update_rate_limit(): return

        uid = message.author.id
        tid = self.user_target_guild.get(uid)

        if tid in self.support_config:
            await self.process_forward(message.author, message.content, tid)
        else:
            view = ServerSelectView(self.bot, uid, self)
            if not view.children: return await message.channel.send("❌ 找不到可用伺服器。")
            await message.channel.send("📞 請選擇伺服器：", view=view)

    async def process_forward(self, user, question, guild_id):
        guild = self.bot.get_guild(guild_id)
        config = self.support_config.get(guild_id)
        if not guild or not (chan := guild.get_channel(config[0])): return

        embed = discord.Embed(title=f"❓ 來自 {user.name}", description=f"**訊息內容:**\n```\n{question[:1500]}\n```", color=0xf1c40f)
        embed.set_footer(text=f"User ID: {user.id} | {safe_now()}")
        
        view = ReplyView(self)
        if match := re.search(r"(https?://[^\s]+)", question):
            view.add_item(ui.Button(label="🔗 連結", url=match.group(0)))

        mention = f"<@&{config[1]}>" if config[1] else "@here"
        await chan.send(content=mention, embed=embed, view=view)
        await user.send(f"✅ 已送達 **{guild.name}**。")

async def setup(bot):
    await bot.add_cog(SupportCog(bot))
