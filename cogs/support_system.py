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
import io
import json
# =========================
# -- 工具與基礎設定
# =========================
from utils.time_utils import safe_now

DATA_STORAGE_CHANNEL_ID = 1518065055466262649  
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
        
        # 💡 這裡已經幫你把 3 個反引號 ``` 完美關閉，既不會報錯，Discord 灰色框框也最漂亮！
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
        sid = int(interaction.data['values'][0])
        guild_name = self.bot.get_guild(sid).name
        
        # 儲存在記憶體
        self.cog.user_target_guild[self.user_id] = sid
        
        # 💡 自動備份：將最新的對照資料傳送到你的私人頻道
        await self.cog.save_config_to_discord()
        
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
        self.support_config = {}      # 記憶體轉發設定
        self.user_target_guild = {}   # 記憶體用戶選擇目標
        self.transcript_dir = "transcripts"
        self._cd_mapping = commands.CooldownMapping.from_cooldown(1, 7.0, commands.BucketType.user)
        self.temp_file_cache = {}

    async def cog_load(self):
        if not os.path.exists(self.transcript_dir):
            os.makedirs(self.transcript_dir)
        
        # 建立一個背景任務，等機器人準備好（快取載入完畢）再執行還原
        self.bot.loop.create_task(self.safe_load_config())

    async def safe_load_config(self):
        # 等待機器人完全 Ready
        await self.bot.wait_until_ready()
        await self.load_config_from_discord()


     
    async def save_config_to_discord(self, guild_id: int) -> tuple[bool, str]:
        """
        自動將設定備份到指定的私人頻道。
        流程：
        1. 先產生檔案內容並儲存到記憶體中
        2. 嘗試發送到私人頻道
        3. 成功則刪除記憶體的檔案快取
        4. 失敗則啟動第二重救援，從記憶體拿出檔案重試
        5. 二次失敗則回報並寫入 Render 日誌
        """
        # 💡 步驟 1：先產生檔案內容，把檔案暫存到記憶體中
        try:
            payload = {
                "support_config": {str(k): v for k, v in self.support_config.items()},
                "user_target_guild": {str(k): v for k, v in self.user_target_guild.items()}
            }
            data_str = json.dumps(payload, ensure_ascii=False, indent=4)
            
            # 將產生的檔案內容寫入記憶體暫存 (temp_file_cache)
            self.temp_file_cache[guild_id] = data_str
            print(f"📦 [記憶體快取] 已成功將設定檔備份封包儲存至記憶體暫存區。")
        except Exception as e:
            err_msg = f"記憶體包裝設定檔失敗: {e}"
            print(f"❌ [Render 日誌] {err_msg}")
            return False, err_msg

        # 💡 步驟 2：嘗試傳送到你的私人頻道
        channel = self.bot.get_channel(DATA_STORAGE_CHANNEL_ID)
        if not channel:
            try:
                # 快取沒有就強制向 Discord API 撈取
                channel = await self.bot.fetch_channel(DATA_STORAGE_CHANNEL_ID)
            except Exception as e:
                # 第一次嘗試讀取頻道就失敗，立刻觸發「從記憶體拿出檔案重試」救援機制
                return await self.handle_backup_retry(guild_id, f"無法定位私人頻道: {e}")

        try:
            # 從記憶體中讀取快取好的檔案字串
            file_data = self.temp_file_cache.get(guild_id)
            if not file_data:
                raise ValueError("記憶體暫存區中找不到資料")

            # 建立虛擬檔案
            data_file = discord.File(io.StringIO(file_data), filename="bot_config_backup.json")
            
            # 發送檔案與文字
            await channel.send(
                content="🧪 **[連線與備份測試]** 機器人已成功連線，正在將最新的設定備份檔案發送至此頻道！",
                file=data_file
            )
            
            # 💡 步驟 3：如果傳送成功，就刪除記憶體中的暫存檔案
            self.temp_file_cache.pop(guild_id, None)
            print("✅ [Render 日誌] 備份檔案傳送成功！已將記憶體暫存檔案刪除並清空快取。")
            return True, ""

        except Exception as e:
            # 💡 步驟 4：首次傳送失敗，立刻將檔案從記憶體中拿出來進行第二次備份嘗試
            return await self.handle_backup_retry(guild_id, f"首次發送失敗: {e}")

    async def handle_backup_retry(self, guild_id: int, first_error_msg: str) -> tuple[bool, str]:
        """救援機制：當首次備份失敗時，將檔案從記憶體中拿出來，嘗試第二次傳送"""
        print(f"⚠️ [Render 日誌] 備份傳送意外中斷（原因：{first_error_msg}）。正在從記憶體拿出檔案，執行二次救援嘗試...")
        
        # 💡 從記憶體保險箱中拿出剛才暫存的檔案資料
        cached_data = self.temp_file_cache.get(guild_id)
        if not cached_data:
            err_msg = "記憶體救援失敗：暫存快取中找不到可用的檔案封包！"
            print(f"❌ [Render 日誌] {err_msg}")
            return False, err_msg

        try:
            # 重新強制撈取頻道連線
            channel = await self.bot.fetch_channel(DATA_STORAGE_CHANNEL_ID)
            
            # 重新建立虛擬檔案
            data_file = discord.File(io.StringIO(cached_data), filename="bot_config_backup.json")
            
            # 進行二次發送
            await channel.send(
                content="🔄 **[備份救援重試]** 首次備份因意外中斷，已從記憶體中復原設定檔並重新上傳成功！",
                file=data_file
            )
            
            # 重試成功，刪除記憶體中的檔案暫存
            self.temp_file_cache.pop(guild_id, None)
            print("✅ [Render 日誌] 備份救援成功！記憶體中的暫存檔案已安全刪除。")
            return True, ""
            
        except Exception as retry_error:
            # 💡 步驟 5：如果還是儲存失敗，直接回報詳細錯誤日誌，並印到 Render 控制台中
            final_err_msg = f"第二次備份重試依然失敗。首次錯誤: {first_error_msg} | 二次重試錯誤: {retry_error}"
            
            # 印到 Render 控制台日誌（方便你去後台查看）
            print(f"❌ [Render 日誌] 客服系統備份徹底失敗！這將導致重開機後設定流失！詳細錯誤日誌: {final_err_msg}")
            
            # 傳回 False 與完整錯誤原因
            return False, final_err_msg
                
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

        gid, cid, rid = interaction.guild.id, channel.id, (role.id if role else None)
        self.support_config[gid] = [cid, rid]
        
        # 💡 呼叫備份並傳入當前伺服器 ID，執行我們設計的記憶體救援流程
        success, err = await self.save_config_to_discord(gid)
        
        if success:
            await interaction.followup.send(f"✅ 已成功將 {channel.mention} 設定為轉發頻道，並已同步永久儲存於私人頻道！", ephemeral=True)
        else:
            # 💡 如果第二次也儲存失敗，直接跟用戶（管理員）報告錯誤，告知去 Render 日誌查看詳細細節
            await interaction.followup.send(
                f"❌ 轉發設定完成，但**二次備份嘗試皆失敗**，設定無法永久保存！\n"
                f"**錯誤報告：** `{err}`\n"
                f"⚠️ 請檢查機器人在私人頻道中是否被關閉了「檢視頻道」、「傳送訊息」或「嵌入連結與發送檔案」權限！詳細崩潰資訊已同步紀錄至 Render 控制台日誌中。", 
                ephemeral=True
            )

        
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
        """一則訊息到底：更新所有資訊並加上產檔連結 (完整詳細資訊版)"""
        try:
            file_name = f"transcript_{user_id}.txt"
            file_path = os.path.join(self.transcript_dir, file_name)
            
            # 1. 產檔紀錄對話
            if channel:
                msgs = []
                async for m in channel.history(limit=1000, oldest_first=True):
                    # 過濾掉系統提示訊息，保留對話內容
                    if m.author.bot and not m.content.startswith("✨"): continue
                    msgs.append(f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {m.author.display_name}: {m.content}")
                
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"--- Chat Log (User ID: {user_id}) ---\n" + "\n".join(msgs))
                
                await channel.delete()

            # 2. 從原始 Embed 獲取數據 (確保資訊不遺失)
            old_embed = origin_msg.embeds[0]
            user_name = old_embed.title.replace("❓ 來自 ", "")
            # 解析原始內容
            content_match = re.search(r"```\n?(.*?)\n?```", old_embed.description, re.DOTALL)
            content = content_match.group(1) if content_match else "無法解析內容"
            # 解析原本的發送時間
            send_time = old_embed.footer.text.split("|")[1].strip() if "|" in old_embed.footer.text else "未知時間"

            # 3. 構建「完整詳細資訊」總結 Embed
            summary_embed = discord.Embed(title="✅ 案件已處理", color=discord.Color.dark_gray())
            summary_embed.description = f"**處理人員：** {closer_name}\n**處理時間：** `{safe_now()}`"
            
            summary_embed.add_field(name="👤 用戶資訊", value=f"名稱：**{user_name}**\nID：`{user_id}`", inline=True)
            summary_embed.add_field(name="🏢 伺服器資訊", value=f"目標：**{origin_msg.guild.name}**\nID：`{origin_msg.guild.id}`", inline=True)
            summary_embed.add_field(name="📊 案件統計", value=f"發送時間：`{send_time}`\n狀態：`已結案`", inline=False)
            summary_embed.add_field(name="📝 原始問題回顧", value=f"```\n{content[:800]}\n```", inline=False)
            
            summary_embed.set_footer(text=f"處理者：{closer_name} | 案件 ID：{origin_msg.id}")

            # 4. 建立按鈕 View
            view = ui.View(timeout=None)
            # 加上跳轉原始訊息的連結按鈕
            jump_url = f"https://discord.com/channels/{origin_msg.guild.id}/{origin_msg.channel.id}/{origin_msg.id}"
            view.add_item(ui.Button(label="查看訊息紀錄", style=discord.ButtonStyle.link, url=jump_url))

            # 5. 上傳紀錄檔並提供下載按鈕
            if os.path.exists(file_path):
                log_chan = self.bot.get_channel(1470291339118641253) # 確保這裡 ID 正確
                if log_chan:
                    file_msg = await log_chan.send(content=f"📁 案件結案紀錄: `{user_name}` (`{user_id}`)", file=discord.File(file_path))
                    # 將附件網址做成按鈕
                    view.add_item(ui.Button(label="📄 下載對話紀錄", style=discord.ButtonStyle.link, url=file_msg.attachments[0].url))
            
            # 最後更新原始的管理端訊息
            await origin_msg.edit(embed=summary_embed, view=view)
            
        except Exception as e:
            print(f"❌ 執行結案詳細程序失敗: {e}")

async def setup(bot): 
    await bot.add_cog(SupportCog(bot))
