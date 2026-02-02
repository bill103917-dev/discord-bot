import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import io
import logging
from cryptography.fernet import Fernet

# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BackupSystem")

# ==========================================
# 1. UI 組件 (保持不變)
# ==========================================

class RestorePreCheckView(discord.ui.View):
    def __init__(self, cog, key: str, backup_file: discord.Attachment):
        super().__init__(timeout=None)
        self.cog = cog
        self.key = key
        self.backup_file = backup_file

    @discord.ui.button(label="我已經設定完成", style=discord.ButtonStyle.green)
    async def confirm_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ 重新檢查環境並繼續...", view=None)
        # 改為直接 await 或更安全的 Task 管理
        asyncio.create_task(self.cog._execute_restore(interaction, self.key, self.backup_file))

    @discord.ui.button(label="跳過特殊頻道，建立一般頻道", style=discord.ButtonStyle.blurple)
    async def ignore_special(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ 收到，將略過公告/論壇/舞台頻道執行還原...", view=None)
        asyncio.create_task(self.cog._execute_restore(interaction, self.key, self.backup_file, skip_special=True))

    @discord.ui.button(label="取消復原，保持現狀", style=discord.ButtonStyle.gray)
    async def cancel_restore(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ 已取消還原操作。", view=None)

class DeleteSafeChannelView(discord.ui.View):
    def __init__(self, channel, original_name):
        super().__init__(timeout=None)
        self.channel = channel
        self.original_name = original_name

    @discord.ui.button(label="🗑️ 刪除此安全頻道", style=discord.ButtonStyle.red)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.channel.delete()
        except:
            pass

    @discord.ui.button(label="↩️ 保留並恢復原名", style=discord.ButtonStyle.gray)
    async def keep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.channel.edit(name=self.original_name)
        await interaction.response.edit_message(content=f"✅ 頻道名稱已恢復。", view=None)

# ==========================================
# 2. 備份系統核心 Cog (修正版)
# ==========================================

class BackupSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_overwrites_data(self, channel):
        overwrites_data = []
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Role):
                allow, deny = overwrite.pair()
                overwrites_data.append({
                    "role_name": target.name,
                    "allow": allow.value,
                    "deny": deny.value
                })
        return overwrites_data

    async def _safe_delay(self, seconds):
        """自定義延遲，確保不會阻塞 Event Loop"""
        await asyncio.sleep(seconds)

    async def _delete_all_existing_data(self, guild, safe_id, status_msg):
        """工具：清理伺服器 (大幅強化防限流)"""
        channels = [c for c in guild.channels if c.id != safe_id]
        for i, ch in enumerate(channels, 1):
            try:
                await ch.delete()
                # 提高延遲，避免連續刪除觸發 Cloudflare
                await self._safe_delay(0.8) 
                if i % 5 == 0: 
                    await status_msg.edit(content=f"🧹 清理舊頻道中... ({i}/{len(channels)})")
                    await self._safe_delay(1.5)
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    retry_after = getattr(e, 'retry_after', 30)
                    print(f"⚠️ 刪除頻道遇到限流，等待 {retry_after} 秒...")
                    await asyncio.sleep(retry_after)
                continue
            except: pass
        
        roles = [r for r in guild.roles if not r.is_default() and not r.managed and r < guild.me.top_role]
        for r in roles:
            try: 
                await r.delete()
                await self._safe_delay(0.5)
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(getattr(e, 'retry_after', 10))
                continue
            except: pass

    async def _execute_restore(self, interaction: discord.Interaction, key: str, backup_file: discord.Attachment, skip_special: bool = False):
        guild = interaction.guild
        safe_channel = interaction.channel
        original_name = safe_channel.name

        # 1. 解密資料
        try:
            f = Fernet(key.encode())
            raw_data = await backup_file.read()
            server_data = json.loads(f.decrypt(raw_data).decode())
        except Exception as e:
            logger.error(f"解密出錯: {e}")
            return await safe_channel.send("❌ **解密失敗**：密鑰或檔案錯誤。")

        # 2. 環境預檢 (保持原有邏輯)
        special_types = [5, 13, 15] 
        has_special = any(c["type"] in special_types for c in server_data["channels"])
        
        if has_special and not guild.rules_channel and not skip_special:
            view = RestorePreCheckView(self, key, backup_file)
            embed = discord.Embed(title="🚫 還原預檢未通過", color=discord.Color.red(),
                description="備份檔包含特殊頻道，但伺服器尚未開啟「社群」功能。")
            return await safe_channel.send(embed=embed, view=view)

        # 3. 開始還原程序
        status_msg = await safe_channel.send("🚀 **預檢通過！開始清理伺服器...**")
        try:
            await safe_channel.edit(name="🔒-還原安全區")
            await self._delete_all_existing_data(guild, safe_channel.id, status_msg)

            # 4. 重建身份組
            role_map = {}
            total_roles = len(server_data["roles"])
            for i, r in enumerate(server_data["roles"], 1):
                try:
                    await status_msg.edit(content=f"👥 **[2/4] 重建身份組... ({i}/{total_roles})**")
                    new_role = await guild.create_role(
                        name=r["name"], permissions=discord.Permissions(r["permissions"]),
                        color=discord.Color(r["color"]), hoist=r["hoist"], mentionable=r["mentionable"]
                    )
                    role_map[r["name"]] = new_role
                    await self._safe_delay(1.0) # 身份組變動很大，建議 1 秒
                except discord.errors.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(getattr(e, 'retry_after', 30))
            
            # 5. 兩階段重建頻道 (加入更嚴格的 429 處理)
            all_ch = server_data["channels"]
            cat_map = {}
            cats = [c for c in all_ch if c["type"] == discord.ChannelType.category.value]
            others = [c for c in all_ch if c["type"] != discord.ChannelType.category.value]
            if skip_special:
                others = [c for c in others if c["type"] not in special_types]

            # 5.1 建立分類
            for i, c in enumerate(cats, 1):
                await status_msg.edit(content=f"📂 **[3/4] 重建分類... ({i}/{len(cats)})**")
                ow = { (role_map.get(o["role_name"]) or guild.default_role): discord.PermissionOverwrite.from_pair(discord.Permissions(o["allow"]), discord.Permissions(o["deny"])) for o in c["overwrites"] if o["role_name"] in role_map or o["role_name"] == "@everyone" }
                new_cat = await guild.create_category(name=c["name"], overwrites=ow)
                cat_map[c["name"]] = new_cat
                await self._safe_delay(1.2)

            # 5.2 建立一般頻道
            for i, c in enumerate(others, 1):
                await status_msg.edit(content=f"📢 **[4/4] 重建頻道... ({i}/{len(others)})**")
                ow = { (role_map.get(o["role_name"]) or guild.default_role): discord.PermissionOverwrite.from_pair(discord.Permissions(o["allow"]), discord.Permissions(o["deny"])) for o in c.get("overwrites", []) if o["role_name"] in role_map or o["role_name"] == "@everyone" }
                p_cat = cat_map.get(c["category_name"])
                
                try:
                    cv = c["type"]
                    if cv in [0, 5]:
                        ch = await guild.create_text_channel(name=c["name"], category=p_cat, overwrites=ow, topic=c.get("topic"), nsfw=c.get("nsfw", False))
                        if cv == 5:
                            try: await ch.edit(type=discord.ChannelType.news)
                            except: pass
                    elif cv == 2:
                        await guild.create_voice_channel(name=c["name"], category=p_cat, overwrites=ow, user_limit=c.get("user_limit"), bitrate=c.get("bitrate"))
                    elif cv == 13:
                        await guild.create_stage_channel(name=c["name"], category=p_cat, overwrites=ow)
                    elif cv == 15:
                        await guild.create_forum_channel(name=c["name"], category=p_cat, overwrites=ow, topic=c.get("topic"))
                    
                    await self._safe_delay(1.5) # 建立頻道頻率最高，設為 1.5 秒
                except discord.errors.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(getattr(e, 'retry_after', 30))
                    logger.error(f"頻道 {c['name']} 失敗: {e}")

            await status_msg.delete()
            reminders = f"\n📌 **手動提醒：** 請檢查規則頻道設定。" if server_data.get("rules_channel_name") else ""
            await safe_channel.send(f"🎉 **伺服器還原結束！**{reminders}", view=DeleteSafeChannelView(safe_channel, original_name))

        except Exception as e:
            await safe_channel.send(f"❌ **還原中斷**：遇到不可預期錯誤: {e}")
            logger.error(f"還原重大錯誤: {e}")

    # --- 斜線指令 ---
    @app_commands.command(name="備份伺服器", description="加密備份伺服器配置與頻道")
    @app_commands.default_permissions(administrator=True)
    async def backup_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            guild = interaction.guild
            roles_data = [{"name": r.name, "permissions": r.permissions.value, "color": r.color.value, "hoist": r.hoist, "mentionable": r.mentionable} for r in guild.roles if not r.is_default() and not r.managed]
            channels_data = []
            for ch in sorted(guild.channels, key=lambda c: c.position):
                channels_data.append({
                    "name": ch.name, "type": ch.type.value, "category_name": ch.category.name if ch.category else None,
                    "topic": getattr(ch, 'topic', None), "nsfw": getattr(ch, 'nsfw', False),
                    "user_limit": getattr(ch, 'user_limit', None), "bitrate": getattr(ch, 'bitrate', None),
                    "overwrites": self._get_overwrites_data(ch)
                })
            data = {"roles": roles_data, "channels": channels_data, "rules_channel_name": guild.rules_channel.name if guild.rules_channel else None}
            key = Fernet.generate_key()
            encrypted = Fernet(key).encrypt(json.dumps(data).encode())
            file = discord.File(io.BytesIO(encrypted), filename=f"backup-{guild.name}.bin")
            await interaction.user.send(f"🔐 **伺服器備份完成**\n密鑰: `{key.decode()}`", file=file)
            await interaction.followup.send("✅ 備份已私訊。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 備份失敗: {e}", ephemeral=True)

    @app_commands.command(name="還原備份", description="還原伺服器結構")
    @app_commands.default_permissions(administrator=True)
    async def restore_backup(self, interaction: discord.Interaction, key: str, backup_file: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        # 啟動非阻塞任務
        self.bot.loop.create_task(self._execute_restore(interaction, key, backup_file))
        await interaction.followup.send("⏳ 還原程序啟動中，請查看頻道訊息。", ephemeral=True)

async def setup(bot):
    await bot.add_cog(BackupSystem(bot))