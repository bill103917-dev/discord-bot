import discord
from discord import app_commands, Interaction
from discord.ext import commands
from google import genai  # 使用最新版 google-genai 套件
import os
import json
import psycopg2
import psycopg2.extras

class GeminiSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 儲存運作中的對話 Session {channel_id: chat_session}
        self.ai_chats = {}
        
        # --- 資料庫設定 ---
        self.db_url = os.getenv("DATABASE_URL")
        self._init_db()

        # --- Gemini 配置 (新版 SDK 語法) ---
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model_id = "gemini-1.5-flash"

    # ==================== 資料庫操作 ====================

    def _init_db(self):
        """確保資料表存在"""
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ai_memory (
                        channel_id BIGINT PRIMARY KEY,
                        history JSONB
                    );
                """)
                conn.commit()

    def _load_db_history(self, channel_id):
        """從資料庫加載歷史紀錄"""
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT history FROM ai_memory WHERE channel_id = %s", (channel_id,))
                row = cur.fetchone()
                return row['history'] if row else []

    def _save_db_history(self, channel_id, history):
        """儲存歷史紀錄至資料庫 (Upsert)"""
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ai_memory (channel_id, history) 
                    VALUES (%s, %s)
                    ON CONFLICT (channel_id) 
                    DO UPDATE SET history = EXCLUDED.history;
                """, (channel_id, json.dumps(history)))
                conn.commit()

    # ==================== 指令部分 ====================

    @app_commands.command(name="ai_chat", description="開啟或關閉 AI 自動對話功能")
    @app_commands.describe(action="選擇開啟或關閉")
    @app_commands.choices(action=[
        app_commands.Choice(name="開啟", value="enable"),
        app_commands.Choice(name="關閉", value="disable")
    ])
    async def ai_chat(self, interaction: Interaction, action: str):
        # 權限檢查
        is_special = interaction.user.id in getattr(self.bot, 'MIMIC_USER_IDS', [])
        if not interaction.user.guild_permissions.administrator and not is_special:
            return await interaction.response.send_message("❌ 你沒有權限設定此功能", ephemeral=True)

        if action == "enable":
            history = self._load_db_history(interaction.channel_id)
            # 新版 SDK 建立對話方式
            self.ai_chats[interaction.channel_id] = self.client.chats.create(
                model=self.model_id,
                config={'history': history}
            )
            
            status = "📚 已恢復永久記憶" if history else "🆕 已開啟新對話"
            await interaction.response.send_message(
                f"✨ **AI 對話功能已啟用 ({status})**\n"
                "📌 使用 `-` 開頭的訊息我會忽略。\n"
                "🤖 模型版本: `Gemini 1.5 Flash`"
            )
        else:
            self.ai_chats.pop(interaction.channel_id, None)
            await interaction.response.send_message("❌ 已關閉本頻道 AI 對話功能。", ephemeral=True)

    @app_commands.command(name="ai_clear", description="清空本頻道的 AI 記憶")
    async def ai_clear(self, interaction: Interaction):
        is_special = interaction.user.id in getattr(self.bot, 'MIMIC_USER_IDS', [])
        if not interaction.user.guild_permissions.administrator and not is_special:
            return await interaction.response.send_message("❌ 權限不足", ephemeral=True)

        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_memory WHERE channel_id = %s", (interaction.channel_id,))
                conn.commit()
        
        if interaction.channel_id in self.ai_chats:
            self.ai_chats[interaction.channel_id] = self.client.chats.create(model=self.model_id)
            
        await interaction.response.send_message("🧹 該頻道的 AI 記憶已完全清空。", ephemeral=True)

    # ==================== 監聽回覆 ====================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        
        # 除錯追蹤
        print(f"--- 收到訊息 ---")
        print(f"內容: '{message.content}'")
        print(f"頻道 ID: {message.channel.id}")
        print(f"是否在 AI 監聽清單: {message.channel.id in self.ai_chats}")

        if message.channel.id not in self.ai_chats: return

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id not in self.ai_chats or message.content.startswith("-"):
            return

        chat = self.ai_chats[message.channel_id]

        async with message.channel.typing():
            try:
                # 新版 SDK 的發送訊息語法
                response = await self.bot.loop.run_in_executor(
                    None, lambda: chat.send(message.content)
                )
                
                if response.text:
                    await message.reply(response.text[:2000])
                    
                    # 將最新的歷史紀錄存回資料庫
                    updated_history = []
                    for h in chat.history:
                        updated_history.append({
                            "role": h.role,
                            "parts": [{"text": p.text} for p in h.parts]
                        })
                    self._save_db_history(message.channel.id, updated_history)
                    
            except Exception as e:
                error_msg = str(e)
                # 恢復為你要求的詳細錯誤處理邏輯
                if "429" in error_msg:
                    await message.reply("⚠️ **說話太快我忙不過來 **\n請稍等幾秒鐘後再試一次。")
                elif "finish_reason: SAFETY" in error_msg or "blocked" in error_msg:
                    await message.reply("🛡️ **抱歉，根據安全準則，我無法針對這項內容進行回覆。**")
                else:
                    print(f"❌ [Gemini Error] {error_msg}")
                    await message.reply("😵 發生了預料之外的錯誤，請稍後再試。")


async def setup(bot):
    await bot.add_cog(GeminiSystem(bot))
