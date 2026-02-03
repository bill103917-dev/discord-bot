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

# =========================
# UI 組件
# =========================

class RestorePreCheckView(discord.ui.View):
    def __init__(self, cog, key: str, backup_file: discord.Attachment):
        super().__init__(timeout=None)
        self.cog = cog
        self.key = key
        self.backup_file = backup_file

    @discord.ui.button(label="我已經設定完成", style=discord.ButtonStyle.green)
    async def confirm_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ 重新檢查環境並繼續...", view=None)
        asyncio.create_task(self.cog._execute_restore(interaction, self.key, self.backup_file))

    @discord.ui.button(label="跳過特殊頻道，建立一般頻道", style=discord.ButtonStyle.blurple)
    async def ignore_special(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ 收到，將略過公告/論壇/舞台頻道執行還原...", view=None)
        asyncio.create_task(self.cog._execute_restore(interaction, self.key, self.backup_file, skip_special=True))

    @discord.ui.button(label="取消復原", style=discord.ButtonStyle.gray)
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
        try:
            await self.channel.edit(name=self.original_name)
            await interaction.response.edit_message(content="✅ 頻道名稱已恢復。", view=None)
        except:
            pass

# =========================
# 備份系統核心 Cog
# =========================

class BackupSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.is_restoring = False # 🔒 全域鎖定，防止多重還原觸發 1015

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
        """強化版延遲，確保不阻塞連線"""
        await asyncio.sleep(seconds)

    async def _delete_all_existing_data(self, guild, safe_id, status_msg):
        """清理伺服器 (加入間歇性長休眠，避開 Cloudflare 偵測)"""
        channels = [c for c in guild.channels if c.id != safe_id]
        for i, ch in enumerate(channels, 1):
            try:
                await ch.delete()
                await self._safe_delay(1.2) # 增加基礎延遲
                if i % 4 == 0: 
                    await status_msg.edit(content=f"🧹 清理中 (避開限流)... ({i}/{len(channels)})")
                    await self._safe_delay(3.0) # 每刪 4 個停 3 秒
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    retry_after = getattr(e, 'retry_after', 30)
                    await asyncio.sleep(retry_after + 5)
                continue
            except: pass
        
        roles = [r for r in guild.roles if not r.is_default() and not r.managed and r < guild.me.top_role]
        for r in roles:
            try: 
                await r.delete()
                await self._safe_delay(0.8)
            except: pass

    async def _execute_restore(self, interaction: discord.Interaction, key: str, backup_file: discord.Attachment, skip_special: bool = False):
        if self.is_restoring:
            return await interaction.channel.send("⚠️ **警告**：系統正在執行還原中，請勿重複啟動。")

        self.is_restoring = True
        guild = interaction.guild
        safe_channel = interaction.channel
        original_name = safe_channel.name

        try:
            # 1. 解密
            f = Fernet(key.encode())
            raw_data = await backup_file.read()
            server_data = json.loads(f.decrypt(raw_data).decode())

            # 2. 預檢社群功能
            special_types = [5, 13, 15]
            has_special = any(c["type"] in special_types for c in server_data["channels"])
            if has_special and not guild.rules_channel and not skip_special:
                view = RestorePreCheckView(self, key, backup_file)
                embed = discord.Embed(title="🚫 需開啟社群功能", color=discord.Color.red(), description="此備份包含公告/論壇頻道，請先開啟伺服器社群功能。")
                self.is_restoring = False
                return await safe_channel.send(embed=embed, view=view)

            status_msg = await safe_channel.send("🚀 **驗證成功，開始還原程序...**")
            await safe_channel.edit(name="🔒-還原安全區")

            # 3. 清理環境
            await self._delete_all_existing_data(guild, safe_channel.id, status_msg)

            # 4. 重建身份組
            role_map = {}
            for i, r in enumerate(server_data["roles"], 1):
                try:
                    await status_msg.edit(content=f"👥 **[2/4] 重建身份組... ({i}/{len(server_data['roles'])})**")
                    new_role = await guild.create_role(
                        name=r["name"], permissions=discord.Permissions(r["permissions"]),
                        color=discord.Color(r["color"]), hoist=r["hoist"], mentionable=r["mentionable"]
                    )
                    role_map[r["name"]] = new_role
                    await self._safe_delay(1.5)
                except: continue
            
            # 5. 重建頻道
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
                await self._safe_delay(2.0)

            # 5.2 建立內容頻道 (高頻率操作，每 3 個長休)
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
                    
                    await self._safe_delay(2.0)
                    if i % 3 == 0: await self._safe_delay(4.0)
                except Exception as e:
                    logger.error(f"頻道 {c['name']} 失敗: {e}")

            await status_msg.delete()
            await safe_channel.send(f"🎉 **伺服器還原結束！**", view=DeleteSafeChannelView(safe_channel, original_name))

        except Exception as e:
            await safe_channel.send(f"❌ **還原中斷**：{e}")
            logger.error(f"重大錯誤: {e}")
        finally:
            self.is_restoring = False # 🔓 解鎖

    @app_commands.command(name="備份伺服器", description="加密備份伺服器配置")
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
            await interaction.user.send(f"🔐 **備份完成**\n密鑰: `{key.decode()}`", file=file)
            await interaction.followup.send("✅ 備份已私訊。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 備份失敗: {e}", ephemeral=True)

    @app_commands.command(name="還原備份", description="還原伺服器結構")
    @app_commands.default_permissions(administrator=True)
    async def restore_backup(self, interaction: discord.Interaction, key: str, backup_file: discord.Attachment):
        if self.is_restoring:
            return await interaction.response.send_message("⚠️ 系統正在還原中，請稍後再試。", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        self.bot.loop.create_task(self._execute_restore(interaction, key, backup_file))
        await interaction.followup.send("⏳ 還原任務已啟動，請查看頻道訊息。", ephemeral=True)

async def setup(bot):
    await bot.add_cog(BackupSystem(bot))