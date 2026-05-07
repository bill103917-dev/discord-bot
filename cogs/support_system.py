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
            except:
                await interaction.followup.send("❌ 無法私訊用戶。", ephemeral=True)

# =========================
# -- 2. 臨時聊天室控制 (Control View)
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
# -- 3. 臨時聊天室邀請 (Invite View)
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
        
        try: await interaction.response.defer()
        except: return
        
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
                # 修改原本訊息的 View，讓它顯示聊天室連結並保留已處理按鈕
                admin_new_view.clear_items()
                admin_new_view.add_item(ui.Button(label="已開啟對話室", url=channel.jump_url, style=discord.ButtonStyle.link, emoji="💬"))
                
                # 重新加入已處理按鈕，確保 ID 一致
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
            invite_view = ChatInviteView(interaction.user, user_obj, self.cog, origin_msg=interaction.message)
            await user_obj.send(f"🔔 **來自 {interaction.guild.name} 管理員的邀請**", view=invite_view)
            await interaction.followup.send(f"✅ 已對 **{user_obj.name}** 發起邀請。", ephemeral=True)
        except:
            await interaction.followup.send("❌ 邀請失敗。", ephemeral=True)

    @ui.button(label='已處理', style=discord.ButtonStyle.danger, emoji="🛑", custom_id="support_stop_btn")
    async def stop_button(self, interaction: Interaction, button: ui.Button):
        await self.cog.reply_view_stop_callback(interaction)

# =========================
# -- 5. 核心 Cog
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
        """初始化資料庫連線池並建立必要的資料表"""
        if not self.db_url:
            print("❌ [錯誤] 找不到環境變數 DATABASE_URL。請在 Render 的 Environment Variables 設定它。")
            return

        try:
            # 1. 修正網址格式 (Render 預設是 postgres://，但 asyncpg 只認 postgresql://)
            adjusted_url = self.db_url.replace("postgres://", "postgresql://", 1)

            # 2. 建立連線池 (加入 ssl='require' 以確保能連上 Render 的資料庫)
            self.pool = await asyncpg.create_pool(
                adjusted_url,
                min_size=1,
                max_size=3,
                ssl='require', # Render 資料庫通常需要 SSL
                command_timeout=60
            )

            # 3. 測試連線並確保資料表存在
            async with self.pool.acquire() as conn:
                # 建立設定表 (儲存轉發頻道與身份組)
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS support_configs (
                        guild_id BIGINT PRIMARY KEY,
                        channel_id BIGINT,
                        role_id BIGINT
                    )
                ''')
                
                # 建立目標表 (儲存使用者對應的伺服器)
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_targets (
                        user_id BIGINT PRIMARY KEY,
                        guild_id BIGINT
                    )
                ''')
                
                # 4. 預載數據到記憶體 (優化 Bot 反應速度)
                config_rows = await conn.fetch('SELECT * FROM support_configs')
                for r in config_rows:
                    self.support_config[r['guild_id']] = (r['channel_id'], r['role_id'])
                
                target_rows = await conn.fetch('SELECT * FROM user_targets')
                for tr in target_rows:
                    self.user_target_guild[tr['user_id']] = tr['guild_id']
                    
            print("✅ [資料庫] 初始化成功，設定已載入記憶體。")

        except Exception as e:
            print(f"❌ [資料庫] 初始化失敗：{e}")
            self.pool = None  # 確保失敗時 pool 為 None，避免後續指令發生 AttributeError



    async def reply_view_stop_callback(self, interaction: Interaction):
        """處理管理端按下已處理的確認邏輯"""
        try: await interaction.response.defer()
        except: return
        
        old_embed = interaction.message.embeds[0]
        user_id_str = re.search(r"ID: (\d+)", old_embed.footer.text).group(1)
        target_channel = discord.utils.get(interaction.guild.text_channels, topic=f"User ID: {user_id_str}")
        
        if target_channel:
            confirm_view = ui.View(timeout=60)
            confirm_btn = ui.Button(label="管理員要求結案，確認結束", style=discord.ButtonStyle.danger, emoji="⚠️")
            async def force_close_callback(i: Interaction):
                await i.response.send_message("📂 管理員已確認結案...")
                await self.execute_final_close(interaction.message, user_id_str, target_channel, interaction.user.display_name)
            confirm_btn.callback = force_close_callback
            confirm_view.add_item(confirm_btn)
            await target_channel.send(f"⚠️ {interaction.user.mention} 正在嘗試結案，是否同意？", view=confirm_view)
            return await interaction.followup.send("⚠️ 該用戶正在對話室，已發送確認。", ephemeral=True)

        await self.execute_final_close(interaction.message, user_id_str, closer_name=interaction.user.display_name)

    async def execute_final_close(self, origin_msg, user_id, channel=None, closer_name="系統"):
        """一則訊息到底：更新所有資訊並加上產檔連結"""
        try:
            file_name = f"transcript_{user_id}.txt"
            file_path = os.path.join(self.transcript_dir, file_name)
            
            # 1. 產檔紀錄對話
            if channel:
                msgs = []
                async for m in channel.history(limit=1000, oldest_first=True):
                    if m.author.bot and not m.content.startswith("✨"): continue
                    msgs.append(f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {m.author.display_name}: {m.content}")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"--- Chat Log (User ID: {user_id}) ---\n" + "\n".join(msgs))
                await channel.delete()

            # 2. 獲取原始數據
            old_embed = origin_msg.embeds[0]
            user_name = old_embed.title.replace("❓ 來自 ", "")
            content = re.search(r"```\n?(.*?)\n?```", old_embed.description, re.DOTALL).group(1)
            send_time = old_embed.footer.text.split("|")[1].strip() if "|" in old_embed.footer.text else "未知"

            # 3. 構建總結 Embed (包含所有資訊)
            summary_embed = discord.Embed(title="✅ 案件已處理", color=discord.Color.dark_gray())
            summary_embed.description = f"**處理人員：** {closer_name}\n**處理時間：** `{safe_now()}`"
            summary_embed.add_field(name="👤 用戶資訊", value=f"名稱：**{user_name}**\nID：`{user_id}`", inline=True)
            summary_embed.add_field(name="🏢 伺服器資訊", value=f"目標：**{origin_msg.guild.name}**\nID：`{origin_msg.guild.id}`", inline=True)
            summary_embed.add_field(name="📊 統計", value=f"發送時間：`{send_time}`\n狀態：`已結案`", inline=False)
            summary_embed.add_field(name="📝 原始問題", value=f"```\n{content[:800]}\n```", inline=False)
            summary_embed.set_footer(text=f"處理者：{closer_name} | 案件 ID：{origin_msg.id}")

            # 4. 建立按鈕 View
            view = ui.View(timeout=None)
            jump_url = f"https://discord.com/channels/{origin_msg.guild.id}/{origin_msg.channel.id}/{origin_msg.id}"
            view.add_item(ui.Button(label="查看訊息紀錄", style=discord.ButtonStyle.link, url=jump_url))

            if os.path.exists(file_path):
                log_chan = self.bot.get_channel(1470291339118641253) # 📌 這裡請修改為你的 Log 頻道 ID
                if log_chan:
                    file_msg = await log_chan.send(content=f"📁 Log: `{user_name}`", file=discord.File(file_path))
                    view.add_item(ui.Button(label="📄 查看對話紀錄 (下載)", style=discord.ButtonStyle.link, url=file_msg.attachments[0].url))
            
            await origin_msg.edit(embed=summary_embed, view=view)
        except Exception as e: print(f"❌ 結案失敗: {e}")

    @app_commands.command(name="set_support_channel", description="設定轉發頻道")
    @app_commands.default_permissions(manage_guild=True)
    async def set_support_channel(self, interaction: Interaction, channel: discord.TextChannel, role: Optional[discord.Role] = None):
        # 1. 先讓 Discord 知道我們正在處理中，避免 3 秒過期
        await interaction.response.defer(ephemeral=True)

        # 2. 檢查連線池狀態
        if self.pool is None:
            await self.init_db()
            if self.pool is None:
                # 這裡要用 followup，因為前面 defer 過了
                return await interaction.followup.send("❌ 無法連線至資料庫，請檢查 Render 環境變數。", ephemeral=True)

        gid, cid, rid = interaction.guild.id, channel.id, (role.id if role else None)
        self.support_config[gid] = (cid, rid)
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    'INSERT INTO support_configs (guild_id, channel_id, role_id) VALUES ($1,$2,$3) '
                    'ON CONFLICT (guild_id) DO UPDATE SET channel_id=$2, role_id=$3', 
                    gid, cid, rid
                )
            # 3. 成功後使用 followup 發送結果
            await interaction.followup.send(f"✅ 已設定 {channel.mention} 為轉發頻道", ephemeral=True)
        except Exception as e:
            print(f"❌ 寫入資料庫失敗: {e}")
            await interaction.followup.send(f"❌ 寫入資料庫失敗，錯誤已記錄在 Log。", ephemeral=True)

    @app_commands.command(name="select_server", description="選擇或切換您要發送問題的目標伺服器")
    @app_commands.guild_only(False) # 允許在私訊使用
    async def select_server(self, interaction: discord.Interaction):
        # 確保指令是在私訊中使用
        if interaction.guild is not None:
            return await interaction.response.send_message("❌ 此指令只能在機器人私訊中使用。", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        
        # 找出用戶所在的伺服器，且該伺服器有設定客服頻道
        user_id = interaction.user.id
        shared_guilds = [
            g for g in self.bot.guilds 
            if g.get_member(user_id) and g.id in self.support_config
        ]

        if not shared_guilds:
            return await interaction.followup.send("❌ 找不到您加入且有開啟客服系統的伺服器。", ephemeral=True)

        # 呼叫你原本就有的 ServerSelectView
        view = ServerSelectView(self.bot, user_id, self)
        await interaction.followup.send("📞 請從下方選單選擇目標伺服器：", view=view, ephemeral=True)


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild: return
        if self._cd_mapping.get_bucket(message).update_rate_limit(): return
        
        uid = message.author.id
        tid = self.user_target_guild.get(uid)
        
        if tid in self.support_config:
            guild = self.bot.get_guild(tid)
            config = self.support_config.get(tid)
            if not guild or not (chan := guild.get_channel(config[0])): return
            
            # --- 新增：偵測訊息中的連結 ---
            # 這個正規表示法會抓取訊息中的第一個網址
            url_pattern = r'https?://[^\s]+'
            found_url = re.search(url_pattern, message.content)
            
            embed = discord.Embed(
                title=f"❓ 來自 {message.author.name}", 
                description=f"**訊息內容:**\n```\n{message.content[:1500]}\n```", 
                color=0xf1c40f
            )
            embed.set_footer(text=f"User ID: {uid} | {safe_now()}")
            
            # 建立管理端按鈕
            view = ReplyView(self)
            
            # 如果有找到連結，動態在 View 裡面加上一個跳轉按鈕
            if found_url:
                jump_url = found_url.group(0)
                # 這裡將按鈕插入到最前面
                link_button = ui.Button(label="點我開啟連結", url=jump_url, emoji="🔗", row=1)
                view.add_item(link_button)
            
            mention = f"<@&{config[1]}>" if config[1] else "@here"
            await chan.send(content=mention, embed=embed, view=view)
            await message.author.send(f"✅ 您的訊息已送達 **{guild.name}** 管理端。")
        else:
            view = ServerSelectView(self.bot, uid, self)
            if view.children: 
                await message.channel.send("📞 請選擇伺服器：", view=view)


class ServerSelectView(ui.View):
    def __init__(self, bot, user_id, cog):
        super().__init__(timeout=60)
        shared = [g for g in bot.guilds if g.get_member(user_id) and g.id in cog.support_config]
        if shared:
            options = [discord.SelectOption(label=g.name, value=str(g.id)) for g in shared]
            select = ui.Select(options=options)
            async def callback(interaction):
                sid = int(interaction.data['values'][0])
                cog.user_target_guild[user_id] = sid
                async with cog.pool.acquire() as conn:
                    await conn.execute('INSERT INTO user_targets VALUES ($1,$2) ON CONFLICT (user_id) DO UPDATE SET guild_id=$2', user_id, sid)
                await interaction.response.edit_message(content=f"✅ 已設定目標：**{bot.get_guild(sid).name}**", view=None)
            select.callback = callback
            self.add_item(select)

async def setup(bot): 
    await bot.add_cog(SupportCog(bot))
