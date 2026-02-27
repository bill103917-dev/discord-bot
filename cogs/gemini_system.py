import os # 記得在最上面 import o
import discord
from discord import app_commands, Interaction
from discord.ext import commands
import google.generativeai as genai
from typing import Optional

class GeminiSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 儲存每個頻道的對話 Session {channel_id: chat_session}
        self.ai_chats = {}
        
        # --- Gemini 配置 ---
        # 請替換為你拿到的 API KEY
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

        
        self.generation_config = {
            "temperature": 0.9,
            "top_p": 1,
            "top_k": 1,
            "max_output_tokens": 2048,
        }
        
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=self.generation_config
        )

    @app_commands.command(name="ai_chat", description="開啟或關閉本頻道的 AI 自動對話功能")
    @app_commands.describe(action="選擇開啟或關閉")
    @app_commands.choices(action=[
        app_commands.Choice(name="開啟", value="enable"),
        app_commands.Choice(name="關閉", value="disable")
    ])
    async def ai_chat(self, interaction: Interaction, action: str):
        # 權限檢查：管理員或 MIMIC_USER_IDS 名單
        is_special = interaction.user.id in getattr(self.bot, 'MIMIC_USER_IDS', [])
        if not interaction.user.guild_permissions.administrator and not is_special:
            return await interaction.response.send_message("❌ 你沒有權限設定此功能", ephemeral=True)

        if action == "enable":
            # 初始化該頻道的對話 (開啟新對話)
            self.ai_chats[interaction.channel_id] = self.model.start_chat(history=[])
            
            # 按照你的要求設定啟動訊息
            await interaction.response.send_message(
                "✨ **本頻道已啟用 AI 對話功能**\n"
                "💡 說話會有 AI 回你，就像開啟一個新對話。\n"
                "📌 若不想讓機器人回覆，請在訊息開頭使用 `-`。\n"
                "🤖 AI 提供: `Gemini (Google AI)`"
            )
        else:
            if interaction.channel_id in self.ai_chats:
                del self.ai_chats[interaction.channel_id]
                await interaction.response.send_message("❌ 本頻道已停用 AI 對話功能。", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ 本頻道原本就未開啟 AI 功能。", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 1. 排除機器人自己
        if message.author.bot:
            return
        
        # 2. 檢查該頻道是否在啟用名單中
        if message.channel.id not in self.ai_chats:
            return
        
        # 3. 排除開頭為 "-" 的訊息
        if message.content.startswith("-"):
            return

        # 取得該頻道的 Session
        chat_session = self.ai_chats[message.channel_id]

        # 顯示正在輸入中
        async with message.channel.typing():
            try:
                # 呼叫 Gemini API (非同步執行避免卡頓)
                response = await self.bot.loop.run_in_executor(
                    None, lambda: chat_session.send_message(message.content)
                )
                
                if response.text:
                    # 分割長訊息 (Discord 限制 2000 字)
                    reply_text = response.text[:2000]
                    await message.reply(reply_text)
                    
            except Exception as e:
                # 錯誤處理 (例如 API 限制或內容過濾)
                print(f"Gemini System Error: {e}")

async def setup(bot):
    await bot.add_cog(GeminiSystem(bot))
