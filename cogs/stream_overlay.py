import discord
from discord import app_commands, Interaction
from discord.ext import commands
from discord.ui import Button, View
import json
import os
import logging

log = logging.getLogger("StreamOverlayCog")

class VoiceOverlayControlView(View):
    def __init__(self, cog, owner_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.owner_id = owner_id
        self.channel_id = channel_id

    @discord.ui.button(label="🎛️ 開啟後台控制與設定網頁", style=discord.ButtonStyle.primary, custom_id="btn_overlay_control_page")
    async def open_control_page(self, interaction: Interaction, button: Button):
        render_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000")
        control_url = f"{render_url}/control/{self.channel_id}"
        
        embed = discord.Embed(
            title="🎛️ 專屬後台控制網頁",
            description=f"請點擊下方連結前往控制後台，可複製 OBS 網址、即時調整顯示人數與排版模式：\n\n🔗 [點我前往控制後台]({control_url})",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class StreamOverlayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def broadcast_speaking_status(self, channel_id: str, user_data: dict):
        websockets = getattr(self.bot, "overlay_websockets", {}).get(str(channel_id), [])
        for ws in list(websockets):
            try:
                ws.send(json.dumps(user_data))
                if user_data.get("action") == "close":
                    ws.close()
            except Exception:
                pass

    def fetch_channel_members(self, channel_id_int: int) -> list:
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
                    "speaking": not (m.voice.self_mute or m.voice.mute) if m.voice else False
                })
        return members_list

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        channel = after.channel or before.channel
        if not channel or member.bot:
            return

        channel_id = str(channel.id)
        channel_id_int = channel.id

        active_map = getattr(self.bot, "channel_max_visible", {})
        if channel_id not in active_map and channel_id_int not in active_map:
            return

        is_speaking = False
        if after.channel:
            is_speaking = not (after.self_mute or after.mute)

        avatar_url = member.display_avatar.url
        user_payload = {
            "action": "update",
            "id": str(member.id),
            "name": member.display_name,
            "avatar": avatar_url,
            "speaking": is_speaking
        }
        await self.broadcast_speaking_status(channel_id, user_payload)

    @app_commands.command(name="overlay", description="在語音頻道建立 OBS 實況語音疊加層控制卡片。")
    @app_commands.describe(
        detect_mode="請選擇說話偵測模式 (準確模式 / 通用模式)"
    )
    @app_commands.choices(detect_mode=[
        app_commands.Choice(
            name="🎯 準確模式 (需電腦開著Discord軟體且在此裝置加入語音，能精準說話亮綠圈)", 
            value="rpc"
        ),
        app_commands.Choice(
            name="📱 通用模式 (不需電腦開Discord軟體，支援手機/平板，僅偵測開麥與閉麥)", 
            value="state"
        )
    ])
    async def overlay(self, interaction: Interaction, detect_mode: app_commands.Choice[str] = None):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ 你必須先加入一個語音頻道，才能啟用實況疊加功能！", ephemeral=True)

        voice_channel = interaction.user.voice.channel
        channel_id = voice_channel.id
        
        selected_mode = detect_mode.value if detect_mode else "rpc"
        mode_text = "🎯 準確模式 (PC精準說話)" if selected_mode == "rpc" else "📱 通用模式 (開麥/閉麥)"

        # 記錄頻道初始化設定
        self.bot.channel_max_visible[channel_id] = 3
        self.bot.channel_max_visible[str(channel_id)] = 3
        
        if not hasattr(self.bot, "channel_detect_mode"):
            self.bot.channel_detect_mode = {}
        self.bot.channel_detect_mode[channel_id] = selected_mode
        self.bot.channel_detect_mode[str(channel_id)] = selected_mode

        embed = discord.Embed(
            title="🎙️ 實況語音疊加層 (Voice Overlay) 控制面板",
            description=(
                f"已為語音頻道 **{voice_channel.mention}** 啟動實況視覺疊加層！\n\n"
                f"⚙️ **目前運作模式**：`{mode_text}`\n\n"
                "📌 **使用說明**：\n"
                "點擊下方按鈕可開啟專屬的 **[後台控制網頁]**，複製 OBS 網址、隨時切換模式與調整人數。"
            ),
            color=discord.Color.purple()
        )

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
            content=f"✅ **創建完畢！** 預設模式為 **{mode_text}**。點擊下方按鈕前往控制卡片：",
            view=jump_view,
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(StreamOverlayCog(bot))