import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
import random
import os
import time
import logging

log = logging.getLogger("VerifyCog")


# ==========================================
# 📝 一般模式：驗證回答彈窗 (Modal)
# ==========================================
class VerifyModal(ui.Modal, title="🤖 真人身分驗證"):
    answer_input = ui.TextInput(
        label="請輸入下方題目的正確答案（純數字）",
        placeholder="例如：15",
        required=True,
        max_length=10
    )

    def __init__(self, expected_answer: str, role_id: int, question_text: str):
        super().__init__()
        self.expected_answer = expected_answer
        self.role_id = role_id
        self.answer_input.label = f"題目：{question_text}"

    async def on_submit(self, interaction: Interaction):
        user_answer = str(self.answer_input.value).strip()

        if user_answer == self.expected_answer:
            role = interaction.guild.get_role(self.role_id)
            if not role:
                return await interaction.response.send_message(
                    "❌ 驗證失敗：找不到設定的身分組，請聯繫管理員檢查設定。",
                    ephemeral=True
                )

            try:
                await interaction.user.add_roles(role, reason="通過一般算術真人驗證")
                await interaction.response.send_message(
                    f"✅ 驗證成功！已為您發放 {role.mention} 身分組。",
                    ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ 權限不足：機器人無法發放身分組，請確認機器人身分組順位高於目標身分組。",
                    ephemeral=True
                )
            except Exception as e:
                log.error(f"發放驗證身分組失敗: {e}")
                await interaction.response.send_message(f"❌ 發生錯誤：{e}", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ 答案錯誤（不是 `{user_answer}`），請重新點擊按鈕再試一次。",
                ephemeral=True
            )


# ==========================================
# 🔘 一般模式按鈕視圖
# ==========================================
class MathVerifyButtonView(ui.View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @ui.button(label="🛡️ 點擊進行驗證", style=discord.ButtonStyle.success, custom_id="btn_math_verify")
    async def start_math_verify(self, interaction: Interaction, button: ui.Button):
        role = interaction.guild.get_role(self.role_id)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message(
                f"✅ 您已經擁有 {role.mention} 身分組，無需重複驗證。",
                ephemeral=True
            )

        num1 = random.randint(1, 20)
        num2 = random.randint(1, 20)
        op = random.choice(["+", "-"])

        if op == "+":
            ans = num1 + num2
            question = f"{num1} + {num2} 等於多少？"
        else:
            big, small = max(num1, num2), min(num1, num2)
            ans = big - small
            question = f"{big} - {small} 等於多少？"

        modal = VerifyModal(expected_answer=str(ans), role_id=self.role_id, question_text=question)
        await interaction.response.send_modal(modal)


# ==========================================
# 🌐 網頁認證模式按鈕視圖
# ==========================================
class WebVerifyButtonView(ui.View):
    def __init__(self, cog, guild_id: int, role_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.role_id = role_id

    @ui.button(label="🌐 前往網頁認證", style=discord.ButtonStyle.primary, custom_id="btn_web_verify_open")
    async def open_web_verify(self, interaction: Interaction, button: ui.Button):
        role = interaction.guild.get_role(self.role_id)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message(
                f"✅ 您已經擁有 {role.mention} 身分組，無需重複驗證。",
                ephemeral=True
            )

        base_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000").rstrip("/")
        token = f"{interaction.user.id}_{self.guild_id}_{self.role_id}_{random.randint(100000, 999999)}"
        self.cog.pending_web_verifications[token] = {
            "user_id": interaction.user.id,
            "guild_id": self.guild_id,
            "role_id": self.role_id,
            "expires_at": time.time() + 600
        }

        verify_page_url = f"{base_url}/verify/{token}"
        view = ui.View()
        view.add_item(ui.Button(label="🔗 開啟驗證網頁", url=verify_page_url, style=discord.ButtonStyle.link))

        await interaction.response.send_message(
            "🌐 請點擊下方按鈕開啟認證網頁，完成 Discord 授權後即可自動取得身分組：",
            view=view,
            ephemeral=True
        )


# ==========================================
# 🛡️ 驗證系統核心 Cog
# ==========================================
class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pending_web_verifications = {}
        bot.verify_cog = self

    async def grant_verified_role_by_token(self, token: str, oauth_user_id: str) -> tuple[bool, str]:
        record = self.pending_web_verifications.get(token)
        if not record:
            return False, "驗證連結無效或已使用過，請重新點擊機器人按鈕取得新連結。"

        if time.time() > record["expires_at"]:
            self.pending_web_verifications.pop(token, None)
            return False, "驗證連結已逾時，請重新點擊機器人按鈕。"

        if str(record["user_id"]) != str(oauth_user_id):
            return False, "授權的 Discord 帳號與點擊按鈕的帳號不一致，請使用同一帳號授權。"

        guild = self.bot.get_guild(record["guild_id"])
        if not guild:
            return False, "找不到對應的伺服器。"

        member = guild.get_member(record["user_id"])
        if not member:
            try:
                member = await guild.fetch_member(record["user_id"])
            except Exception:
                return False, "找不到您在伺服器中的成員資料。"

        role = guild.get_role(record["role_id"])
        if not role:
            return False, "找不到設定的目標身分組。"

        try:
            await member.add_roles(role, reason="通過網頁 Discord 授權驗證")
            self.pending_web_verifications.pop(token, None)
            return True, f"已成功為 {member.display_name} 加上「{role.name}」身分組！"
        except discord.Forbidden:
            return False, "機器人缺少管理身分組權限或身分組順位不足。"
        except Exception as e:
            log.error(f"網頁驗證發放身分組失敗: {e}")
            return False, f"發放身分組時發生錯誤：{e}"

    @app_commands.command(name="setup_verify", description="建立驗證面板，可選擇「一般算術模式」或「網頁授權模式」。")
    @app_commands.describe(
        role="驗證通過後要發放的身分組",
        mode="選擇驗證方式（一般算術測試 / 網頁 Discord 授權）",
        title="驗證面板標題（選填）",
        description="驗證面板說明文字（選填）"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="🧮 一般模式（點擊按鈕跳出數學題）", value="math"),
        app_commands.Choice(name="🌐 網頁模式（開網頁 → 跳轉 DC 授權 → 完成給身分組）", value="web")
    ])
    @app_commands.default_permissions(administrator=True)
    async def setup_verify(
        self,
        interaction: Interaction,
        role: discord.Role,
        mode: app_commands.Choice[str],
        title: str = "🛡️ 伺服器身分驗證",
        description: str = "請點擊下方按鈕完成驗證，通過後系統將自動發放身分組。"
    ):
        if not interaction.guild.me.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                "❌ 機器人缺少「管理身分組」權限，請先在伺服器設定中開啟。",
                ephemeral=True
            )

        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                f"❌ 機器人身分組順位必須高於 {role.mention}，請至「伺服器設定 > 身分組」調整順位。",
                ephemeral=True
            )

        selected_mode = mode.value
        mode_label = "🧮 一般算術測試" if selected_mode == "math" else "🌐 網頁 Discord 授權"

        embed = discord.Embed(
            title=title,
            description=f"{description}\n\n🎖️ **通過後身分組**：{role.mention}\n⚙️ **驗證方式**：{mode_label}",
            color=discord.Color.green() if selected_mode == "math" else discord.Color.blurple()
        )
        embed.set_footer(text=f"{interaction.guild.name} • 身分驗證系統")

        if selected_mode == "math":
            view = MathVerifyButtonView(role_id=role.id)
        else:
            view = WebVerifyButtonView(self, guild_id=interaction.guild_id, role_id=role.id)

        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            f"✅ 已在 {interaction.channel.mention} 建立驗證面板（模式：{mode_label}）。",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))