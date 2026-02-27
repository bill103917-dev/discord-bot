import discord
from discord import app_commands, Interaction
from discord.ext import commands
import google.generativeai as genai
import os
import json
import psycopg2
import psycopg2.extras

class GeminiSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 暫存當前運作中的 Session {channel_id: chat_session}
        self.ai_chats = {}
        
        # --- 資料庫設定 ---
        # 讀取 Render 的 DATABASE_URL
        self.db_url = os.getenv("DATABASE_URL")
        self._init_db()

        # --- Gemini 配置 ---
        # 讀取 Render 的 GEMINI_API_KEY
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={
                "temperature": 0.9,
                "top_p": 1,
                "top_k": 1,
                "max_output_tokens": 2048,
            }
        )

    # ==================== 資料庫操作 ====================

    def _init_db(self):
        """在現有資料庫中建立 AI 專用資料表，不影響其他資料"""
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
        """讀取歷史紀錄"""
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT history FROM ai_memory WHERE channel_id = %s", (channel_id,))
                row = cur.fetchone()
                return row['history'] if row else []

    def _save_db_history(self, channel_id, history):
        """存儲歷史紀錄 (Upsert 邏輯)"""
        serializable = []
        for content in history:
            serializable.append({
                "role": content.role,
                "parts": [{"text": part.text} for part in content.parts]
            })
        
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ai_memory (channel_id, history) 
                    VALUES (%s, %s)
                    ON CONFLICT (channel_id) 
                    DO UPDATE SET history = EXCLUDED.history;
                """, (channel_id, json.dumps(serializable)))
                conn.commit()

    # ==================== 指令部分 ====================

    @app_commands.command(name="ai_chat", description="開啟或關閉本頻道的 AI 自動對話功能")
    @app_commands.describe(action="選擇開啟或關閉")
    @app_commands.choices(action=[
        app_commands.Choice(name="開啟", value="enable"),
        app_commands.Choice(name="關閉", value="disable")
    ])
    async def ai_chat(self, interaction: Interaction, action: str):
        # 權限檢查：管理員或特殊名單
        is_special = interaction.user.id in getattr(self.bot, 'MIMIC_USER_IDS', [])
        if not interaction.user.guild_permissions.administrator and not is_special:
            return await interaction.response.send_message("❌ 你沒有權限設定此功能", ephemeral=True)

        if action == "enable":
            # 從資料庫加載記憶
            history = self._load_db_history(interaction.channel_id)
            self.ai_chats[interaction.channel_id] = self.model.start_chat(history=history)
            
            status = "📚 已恢復永久記憶" if history else "🆕 已開啟新對話"
            await interaction.response.send_message(
                f"✨ **本頻道已啟用 AI 對話功能 ({status})**\n"
                "💡 直接說話即可對話，開頭使用 `-` 則 AI 會忽略。\n"
                "🤖 AI 提供: `Gemini (Google AI)`"
            )
        else:
            self.ai_chats.pop(interaction.channel_id, None)
            await interaction.response.send_message("❌ 本頻道已停用 AI 對話功能。", ephemeral=True)

    @app_commands.command(name="ai_clear", description="清空本頻道的 AI 對話記憶")
    async def ai_clear(self, interaction: Interaction):
        # 權限檢查
        is_special = interaction.user.id in getattr(self.bot, 'MIMIC_USER_IDS', [])
        if not interaction.user.guild_permissions.administrator and not is_special:
            return await interaction.response.send_message("❌ 你沒有權解執行此指令", ephemeral=True)

        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM ai_memory WHERE channel_id = %s", (interaction.channel_id,))
                conn.commit()
        
        # 重置當前 Session
        if interaction.channel_id in self.ai_chats:
            self.ai_chats[interaction.channel_id] = self.model.start_chat(history=[])
            
        await interaction.response.send_message("🧹 該頻道的 AI 記憶已完全清空。", ephemeral=True)

    # ==================== 監聽器 ====================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        if message.channel.id not in self.ai_chats: return
        if message.content.startswith("-"): return

        chat_session = self.ai_chats[message.channel_id]

        async with message.channel.typing():
            try:
                # 呼叫 Gemini (非同步)
                response = await self.bot.loop.run_in_executor(
                    None, lambda: chat_session.send_message(message.content)
                )
                
                if response.text:
                    await message.reply(response.text[:2000])
                    # 存入資料庫
                    self._save_db_history(message.channel.id, chat_session.history)
                    
            except Exception as e:
                print(f"Gemini DB Error: {e}")

async def setup(bot):
    await bot.add_cog(GeminiSystem(bot))
