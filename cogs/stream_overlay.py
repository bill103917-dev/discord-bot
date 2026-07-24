import discord
from discord import app_commands, Interaction
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import json
import os
import logging

log = logging.getLogger("StreamOverlayCog")

# ==========================================
# ⚙️ 設定 Modal (修改顯示人數上限)
# ==========================================
class OverlayConfigModal(Modal, title="⚙️ 實況疊加層設定"):
    max_visible = TextInput(
        label="橫向最多顯示人數 (超過自動轉為 +N)",
        placeholder="請輸入數字 (例如: 3 或 4)",
        default="3",
        min_length=1,
        max_length=2
    )

    def __init__(self, cog, channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: Interaction):
        try:
            val = int(self.max_visible.value)
            if val < 1 or val > 10:
                return await interaction.response.send_message("❌ 請輸入 1 到 10 之間的數字！", ephemeral=True)
            
            self.cog.bot.channel_max_visible[self.channel_id] = val
            await interaction.response.send_message(f"✅ 設定已更新！目前橫向最多顯示 **{val}** 人，超過將自動轉換為 `+N`！", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ 請輸入有效的整數數字！", ephemeral=True)

# ==========================================
# 🎛️ 語音頻道聊天室內的按鈕 View
# ==========================================
class VoiceOverlayControlView(View):
    def __init__(self, cog, owner_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id

    def check_permissions(self, interaction: Interaction) -> bool:
        is_owner = interaction.user.id == self.owner_id
        is_admin = interaction.user.guild_permissions.administrator
        return is_owner or is_admin

    @discord.ui.button(label="🔗 顯示畫面連結", style=discord.ButtonStyle.primary, custom_id="btn_overlay_link")
    async def get_link(self, interaction: Interaction, button: Button):
        render_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000")
        overlay_url = f"{render_url}/overlay/{self.channel_id}"
        
        embed = discord.Embed(
            title="🎥 專屬 OBS 語音疊加網址",
            description=f"請複製下方網址並貼入 OBS 的「瀏覽器來源 (Browser Source)」：\n`{overlay_url}`",
            color=discord.Color.green()
        )
        embed.set_footer(text="💡 此網址為個人專屬私密發送，請勿隨意洩漏給無關人員。")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⚙️ 設定", style=discord.ButtonStyle.secondary, custom_id="btn_overlay_config")
    async def open_config(self, interaction: Interaction, button: Button):
        if not self.check_permissions(interaction):
            return await interaction.response.send_message("❌ 只有**伺服器管理員**或**指令發起者**可以調整設定！", ephemeral=True)
        
        modal = OverlayConfigModal(self.cog, self.channel_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🛑 結束並關閉", style=discord.ButtonStyle.danger, custom_id="btn_overlay_stop")
    async def stop_overlay(self, interaction: Interaction, button: Button):
        if not self.check_permissions(interaction):
            return await interaction.response.send_message("❌ 只有**伺服器管理員**或**指令發起者**可以關閉此服務！", ephemeral=True)
        
        # 1. 向前端網頁廣播關閉訊息
        await self.cog.broadcast_speaking_status(str(self.channel_id), {"action": "close"})

        # 2. 清除設定資料
        if self.channel_id in self.cog.bot.channel_max_visible:
            del self.cog.bot.channel_max_visible[self.channel_id]

        embed = discord.Embed(
            title="🛑 語音疊加層服務已結束",
            description="已成功關閉該頻道的實況疊加控制，網頁已自動斷開並清空。",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=None)

# ==========================================
# 🧩 核心 Cog 模組
# ==========================================
class StreamOverlayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def broadcast_speaking_status(self, channel_id: str, user_data: dict):
        """廣播 JSON 資料給連線該頻道的 WebSocket 網頁"""
        websockets = getattr(self.bot, "overlay_websockets", {}).get(channel_id, [])
        for ws in list(websockets):
            try:
                ws.send(json.dumps(user_data))
            except Exception:
                pass

    def fetch_channel_members(self, channel_id_int: int) -> list:
        """主動搜尋 Discord 語音頻道內的所有線上成員資訊"""
        channel = self.bot.get_channel(channel_id_int)
        if not channel or not isinstance(channel, discord.VoiceChannel):
            return []

        members_list = []
        for m in channel.members:
            if not m.bot:
                members_list.append({
                    "id": str(m.id),
                    "name": m.display_name,
                    "avatar": m.display_avatar.url,
                    "speaking": not m.voice.self_mute if m.voice else True
                })
        return members_list

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        channel = after.channel or before.channel
        if not channel:
            return

        channel_id = str(channel.id)
        if after.channel is None:
            user_payload = {"action": "remove", "id": str(member.id)}
        else:
            avatar_url = member.display_avatar.url
            # 未靜音視為發言高亮狀態
            is_speaking = not after.self_mute if after.channel else True
            user_payload = {
                "action": "update",
                "id": str(member.id),
                "name": member.display_name,
                "avatar": avatar_url,
                "speaking": is_speaking
            }

        await self.broadcast_speaking_status(channel_id, user_payload)

    @app_commands.command(name="overlay", description="在語音頻道建立 OBS 實況語音疊加層控制卡片。")
    async def overlay(self, interaction: Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ 你必須先加入一個語音頻道，才能啟用實況疊加功能！", ephemeral=True)

        voice_channel = interaction.user.voice.channel
        channel_id = voice_channel.id

        self.bot.channel_max_visible[channel_id] = 3

        embed = discord.Embed(
            title="🎙️ 實況語音疊加層 (Voice Overlay) 控制面板",
            description=(
                f"已為語音頻道 **{voice_channel.mention}** 啟動實況視覺疊加層！\n\n"
                "📌 **按鈕權限說明**：\n"
                "• 🔗 **[顯示畫面連結]**：所有人皆可按，獲取專屬 OBS 網址。\n"
                "• ⚙️ **[設定]**：僅限管理員或發起者，調整橫向顯示人數。\n"
                "• 🛑 **[結束並關閉]**：僅限管理員或發起者，關閉本頻道服務與網頁。"
            ),
            color=discord.Color.purple()
        )
        embed.set_footer(text="💡 提示：貼入 OBS 瀏覽器來源即可自動顯示橫向頭像！")

        view = VoiceOverlayControlView(self, owner_id=interaction.user.id, channel_id=channel_id)

        control_message = await voice_channel.send(embed=embed, view=view)

        jump_view = View()
        jump_button = Button(
            label="💬 前往語音聊天室控制卡片",
            style=discord.ButtonStyle.link,
            url=control_message.jump_url
        )
        jump_view.add_item(jump_button)

        await interaction.response.send_message(
            content="✅ **創建完畢！** 點擊下面按鈕前往語音頻道的控制訊息：",
            view=jump_view,
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(StreamOverlayCog(bot))