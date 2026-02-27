import discord
from discord import app_commands, Interaction
from discord.ext import commands
import google.generativeai as genai
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor

class GeminiSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai_chats = {}
        
        # --- 資料庫設定 ---
        self.db_url = os.getenv("DATABASE_URL")
        self._init_db()

        # --- Gemini 配置 ---
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    def _init_db(self):
        """初始化資料庫表格"""
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_memory (
                channel_id BIGINT PRIMARY KEY,
                history JSONB
            );
        """)
        conn.commit()
        cur.close()
        conn.close()

    def _load_db_history(self, channel_id):
        """從資料庫讀取歷史"""
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT history FROM ai_memory WHERE channel_id = %s", (channel_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row['history'] if row else []

    def _save_db_history(self, channel_id, history):
        """將歷史儲存至資料庫"""
        serializable = []
        for content in history:
            serializable.append({
                "role": content.role,
                "parts": [{"text": part.text} for part in content.parts]
            })
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ai_memory (channel_id, history) 
            VALUES (%s, %s)
            ON CONFLICT (channel_id) 
            DO UPDATE SET history = EXCLUDED.history;
        """, (channel_id, json.dumps(serializable)))
        conn.commit()
        cur.close()
        conn.close()
        
        
    @app_commands.command(name="ai_clear", description="清空本頻道的 AI 對話記憶")
    @app_commands.default_permissions(administrator=True)
    async def ai_clear(self, interaction: Interaction):
        # 刪除資料庫紀錄
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        cur.execute("DELETE FROM ai_memory WHERE channel_id = %s", (interaction.channel_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        # 如果當前對話正在運行，重置它
        if interaction.channel_id in self.ai_chats:
            self.ai_chats[interaction.channel_id] = self.model.start_chat(history=[])
            
        await interaction.response.send_message("🧹 已清空本頻道的所有 AI 記憶！", ephemeral=True)

    @app_commands.command(name="ai_chat", description="開啟或關閉本頻道的 AI 自動對話功能")
    @app_commands.describe(action="選擇開啟或關閉")
    @app_commands.choices(action=[
        app_commands.Choice(name="開啟", value="enable"),
        app_commands.Choice(name="關閉", value="disable")
    ])
    async def ai_chat(self, interaction: Interaction, action: str):
        is_special = interaction.user.id in getattr(self.bot, 'MIMIC_USER_IDS', [])
        if not interaction.user.guild_permissions.administrator and not is_special:
            return await interaction.response.send_message("❌ 權限不足", ephemeral=True)

        if action == "enable":
            history = self._load_db_history(interaction.channel_id)
            self.ai_chats[interaction.channel_id] = self.model.start_chat(history=history)
            
            status = "📚 已恢復永久記憶" if history else "🆕 已開啟新對話"
            await interaction.response.send_message(
                f"✨ **本頻道已啟用 AI 對話功能 ({status})**\n"
                f"📌 開頭使用 `-` 可跳過 AI 回覆。\n"
                f"🤖 AI 提供: `Gemini (Google AI)`"
            )
        else:
            self.ai_chats.pop(interaction.channel_id, None)
            await interaction.response.send_message("❌ 已停用 AI 對話 (記憶已安全存入資料庫)。", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id not in self.ai_chats or message.content.startswith("-"):
            return

        chat_session = self.ai_chats[message.channel_id]

        async with message.channel.typing():
            try:
                response = await self.bot.loop.run_in_executor(
                    None, lambda: chat_session.send_message(message.content)
                )
                
                if response.text:
                    await message.reply(response.text[:2000])
                    # 每次對話完同步更新資料庫
                    self._save_db_history(message.channel.id, chat_session.history)
            except Exception as e:
                print(f"PostgreSQL/Gemini Error: {e}")

async def setup(bot):
    await bot.add_cog(GeminiSystem(bot))
