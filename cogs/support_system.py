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

class ChatInviteView(ui.View):
    def __init__(self, sender, receiver, cog):
        super().__init__(timeout=60)
        self.sender = sender     # 發起者 (User/Admin)
        self.receiver = receiver # 接收者
        self.cog = cog

    @ui.button(label='接受邀請', style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.receiver.id:
            return await interaction.response.send_message("這不是給你的邀請。", ephemeral=True)
        
        await interaction.response.send_message("🔄 正在創建臨時聊天室...", ephemeral=True)
        
        # 創建臨時頻道 (假設在特定分類下)
        guild = self.cog.bot.get_guild(self.cog.target_guild_id) # 你的目標伺服器
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.sender: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            self.receiver: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        channel = await guild.create_text_channel(
            name=f"temp-chat-{self.sender.name}",
            overwrites=overwrites,
            topic=f"User ID: {self.sender.id if hasattr(self.sender, 'id') else 'Admin'}"
        )

        # 發送前往按鈕
        view = ui.View()
        view.add_item(ui.Button(label="前往聊天室", url=channel.jump_url))
        
        await interaction.followup.send(f"✅ 對方已同意，請點選下方按鈕前往。", view=view, ephemeral=True)
        await self.sender.send(f"✅ 對方已同意，請點選下方按鈕前往。", view=view)
        
        # 聊天室初始訊息
        await channel.send(f"✨ 臨時聊天室已建立！\n雙方：{self.sender.mention} & {self.receiver.mention}\n點擊下方按鈕可結束對話。", view=TempChatControlView(self.cog))

    @ui.button(label='拒絕', style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: Interaction, button: ui.Button):
        await interaction.response.edit_message(content="❌ 已拒絕邀請。", view=None)
        await self.sender.send(f"❌ {self.receiver.name} 拒絕了您的聊天邀請。")

class TempChatControlView(ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.confirm_state = False

    @ui.button(label='結束此對話', style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="end_chat_btn")
    async def end_chat(self, interaction: Interaction, button: ui.Button):
        if not self.confirm_state:
            self.confirm_state = True
            button.label = "確認結束？ (10秒內再次點擊)"
            button.style = discord.ButtonStyle.danger
            await interaction.response.edit_message(view=self)
            
            # 10秒倒數
            await asyncio.sleep(10)
            if self.confirm_state:
                self.confirm_state = False
                button.label = "結束此對話"
                button.style = discord.ButtonStyle.secondary
                await interaction.edit_original_response(view=self)
        else:
            # 執行結束邏輯
            await interaction.response.send_message("📂 正在產生紀錄並關閉頻道...")
            await self.close_and_transcript(interaction.channel, interaction.user)

    async def close_and_transcript(self, channel, closer):
        messages = []
        async for msg in channel.history(limit=1000, oldest_first=True):
            if msg.author.bot: continue
            time = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            messages.append(f"[{time}] {msg.author.display_name}: {msg.content}")

        # 產生檔案
        file_path = f"transcript_{channel.id}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(messages))

        # 傳送檔案給管理員總結訊息 (假設你有存原始訊息 ID)
        # 這裡會根據你之前的「總結 Embed」進行更新，將「查看紀錄」按鈕連往這個檔案
        
        # 刪除頻道
        await channel.delete()
        # 這裡建議將 file 傳送到一個 log 頻道，然後取得連結給總結按鈕用

# =========================
# -- 修正後的 ReplyView (含總結功能)
# =========================

class ReplyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label='回覆問題', style=discord.ButtonStyle.success, emoji="💬", custom_id="support_reply_btn")
    async def reply_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 您沒有權限。", ephemeral=True)
        
        try:
            embed = interaction.message.embeds[0]
            # 解析 Footer 取得 User ID
            user_id = int(embed.footer.text.split("ID: ")[1].split(" |")[0])
            # 解析 Description 取得內容
            content = embed.description.split("```\n")[1].split("\n```")[0]
            await interaction.response.send_modal(ReplyModal(user_id, content))
        except:
            await interaction.response.send_message("❌ 無法解析訊息。", ephemeral=True)

    @ui.button(label='已處理', style=discord.ButtonStyle.danger, emoji="🛑", custom_id="support_stop_btn")
    async def stop_button(self, interaction: Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 無法操作。", ephemeral=True)
        
        await interaction.response.defer() # 處理時間可能較長，先 defer
        
        # 1. 取得原始資料
        old_embed = interaction.message.embeds[0]
        user_id = old_embed.footer.text.split("ID: ")[1].split(" |")[0]
        user_name = old_embed.title.replace("❓ 來自 ", "")
        content = old_embed.description.split("```\n")[1].split("\n```")[0]
        send_time = old_embed.footer.text.split("| ")[1]
        process_time = safe_now()
        
        # 2. 獲取伺服器資訊 (假設從 Content 或 Embed 獲取)
        guild_name = interaction.guild.name
        guild_id = interaction.guild.id

        # 3. 建立總結 Embed
        summary_embed = discord.Embed(
            title=f"✅ 案件已處理",
            description=f"**處理人員：** {interaction.user.mention}\n**處理時間：** `{process_time}`",
            color=discord.Color.light_grey()
        )
        
        summary_embed.add_field(name="👤 用戶資訊", value=f"名稱: **{user_name}**\nID: `{user_id}`", inline=True)
        summary_embed.add_field(name="🏢 伺服器資訊", value=f"目標: **{guild_name}**\nID: `{guild_id}`", inline=True)
        summary_embed.add_field(name="📊 統計", value=f"發送時間: `{send_time}`\n處理狀態: 已結案", inline=False)
        summary_embed.add_field(name="📝 原始問題", value=f"```\n{content[:500]}\n```", inline=False)
        
        summary_embed.set_footer(text=f"處理者：{interaction.user.display_name} | 結案編號: {interaction.message.id}")

        # 4. 建立新按鈕
        new_view = ui.View(timeout=None)
        
        # 原本的跳轉按鈕
        jump_url = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{interaction.message.id}"
        new_view.add_item(ui.Button(label="查看訊息紀錄", style=discord.ButtonStyle.link, url=jump_url))

        # --- 新增：處理對話紀錄文件 ---
        # 假設你的文件路徑是之前產生的 (例如: transcript_12345.txt)
        file_path = f"transcript_{user_id}.txt" 
        
        if os.path.exists(file_path): # 確保檔案存在才執行
            # 設定一個紀錄存放頻道 (請更換為你的頻道 ID)
            log_channel = interaction.client.get_channel(123456789012345678) 
            
            if log_channel:
                file = discord.File(file_path)
                # 將文件發送到 Log 頻道
                log_msg = await log_channel.send(content=f"📁 案件總結紀錄 | 用戶 ID: `{user_id}`", file=file)
                
                # 取得 Discord 伺服器上的檔案永久連結
                file_url = log_msg.attachments[0].url
                new_view.add_item(ui.Button(label="查看紀錄文件", style=discord.ButtonStyle.link, url=file_url))
                
                # 發送後可以刪除本地暫存檔，節省空間
                # os.remove(file_path) 
        # -----------------------------

        # 如果有原始連結也加上去
        if match := re.search(r"(https?://[^\s]+)", content):
            new_view.add_item(ui.Button(label="打開原始連結", style=discord.ButtonStyle.link, url=match.group(0)))

        # 5. 更新訊息
        await interaction.edit_original_response(content=None, embed=summary_embed, view=new_view)

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
            # 在 SupportCog.init_db 中新增
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS temp_chats (
            channel_id BIGINT PRIMARY KEY,
            user_id BIGINT,
            admin_id BIGINT,
            created_at TIMESTAMP
        )
    ''')


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
