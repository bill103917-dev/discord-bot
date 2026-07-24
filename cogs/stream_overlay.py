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
            description=f"請點擊下方連結前往控制後台，可複製 OBS 網址、即時調整人數限制與關閉服務：\n\n🔗 [點我前往控制後台]({control_url})",
            color=discord.Color.indigo()
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
    async def overlay(self, interaction: Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ 你必須先加入一個語音頻道，才能啟用實況疊加功能！", ephemeral=True)

        voice_channel = interaction.user.voice.channel
        channel_id = voice_channel.id

        self.bot.channel_max_visible[channel_id] = 3
        self.bot.channel_max_visible[str(channel_id)] = 3

        embed = discord.Embed(
            title="🎙️ 實況語音疊加層 (Voice Overlay) 控制面板",
            description=(
                f"已為語音頻道 **{voice_channel.mention}** 啟動實況視覺疊加層！\n\n"
                "📌 **使用方式**：\n"
                "點擊下方按鈕可開啟專屬的 **[後台控制網頁]**，在網頁上可直接複製 OBS 連結、調整人數限制與一鍵關閉服務。"
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
            content="✅ **創建完畢！** 點擊下方按鈕前往控制卡片：",
            view=jump_view,
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(StreamOverlayCog(bot))

eof
📄 5. bot.py 的 WebSocket 訊息處理（支援後台設定連動）
請確認 bot.py 裡的 @sock.route('/ws/<channel_id>') 可以接收來自控制網頁傳過來的 update_config 與 stop_service 訊息：
@sock.route('/ws/<channel_id>')
def overlay_ws(ws, channel_id):
    if channel_id not in overlay_websockets:
        overlay_websockets[channel_id] = []
    overlay_websockets[channel_id].append(ws)
    
    try:
        channel_id_int = int(channel_id) if channel_id.isdigit() else None
        
        is_active = (channel_id in channel_max_visible) or (channel_id_int is not None and channel_id_int in channel_max_visible)
        
        if not is_active:
            ws.send(json.dumps({"action": "close"}))
            return

        while True:
            data_raw = ws.receive()
            if data_raw:
                try:
                    data = json.loads(data_raw)
                    cog = bot.get_cog("StreamOverlayCog")

                    # 1. 前端/後台請求初始化資料
                    if data.get("action") == "request_init":
                        if cog and channel_id_int is not None:
                            members = cog.fetch_channel_members(channel_id_int)
                            ws.send(json.dumps({"action": "init", "members": members}))

                    # 2. 🎛️ 來自控制網頁：拉動 slider 更改人數上限
                    elif data.get("action") == "update_config":
                        new_max = data.get("max_visible", 3)
                        channel_max_visible[channel_id] = new_max
                        if channel_id_int is not None:
                            channel_max_visible[channel_id_int] = new_max
                        # 廣播給所有連結此頻道的網頁 (包含 OBS overlay 畫面)
                        if cog:
                            import asyncio
                            asyncio.run_coroutine_threadsafe(
                                cog.broadcast_speaking_status(str(channel_id), {"action": "config_update", "max_visible": new_max}),
                                bot.loop
                            )

                    # 3. 🛑 來自控制網頁：點擊關閉疊加服務
                    elif data.get("action") == "stop_service":
                        if channel_id in channel_max_visible:
                            del channel_max_visible[channel_id]
                        if channel_id_int is not None and channel_id_int in channel_max_visible:
                            del channel_max_visible[channel_id_int]
                        if cog:
                            import asyncio
                            asyncio.run_coroutine_threadsafe(
                                cog.broadcast_speaking_status(str(channel_id), {"action": "close"}),
                                bot.loop
                            )

                except Exception as e:
                    pass
    except Exception:
        pass
    finally:
        if ws in overlay_websockets.get(channel_id, []):
            overlay_websockets[channel_id].remove(ws)

