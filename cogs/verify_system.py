import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
import random
import os
import secrets
import time
import logging

log = logging.getLogger("VerifySystemCog")


def _get_base_web_url() -> str:
    """取得對外網頁網址，優先讀取環境變數，確保產生正確的驗證連結。"""
    custom_url = (os.getenv("VERIFY_BASE_URL") or "").strip().rstrip("/")
    if custom_url:
        return custom_url

    render_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if render_url:
        return render_url

    return "https://disecord-bot2.onrender.com"


# ==========================================
# 1. 算術驗證彈窗 (Modal)
# ==========================================
class MathVerifyModal(ui.Modal, title="🛡️ 真人身分驗證"):
    answer_input = ui.TextInput(
        label="請輸入下方算術題的正確答案（純數字）",
        placeholder="請輸入數字...",
        required=True,
        max_length=10
    )

    def __init__(self, role_id: int | None, is_test_only: bool = False):
        super().__init__()
        self.role_id = role_id
        self.is_test_only = is_test_only

        # 隨機產生加減法題目（確保減法不出現負數）
        op = random.choice(["+", "-"])
        if op == "+":
            a = random.randint(2, 20)
            b = random.randint(2, 20)
            self.expected_answer = a + b
        else:
            a = random.randint(10, 30)
            b = random.randint(1, a)
            self.expected_answer = a - b

        self.answer_input.label = f"請計算：{a} {op} {b} 等於多少？"

    async def on_submit(self, interaction: Interaction):
        raw_val = str(self.answer_input.value).strip()
        if not raw_val.isdigit() or int(raw_val) != self.expected_answer:
            return await interaction.response.send_message(
                f"❌ 驗證失敗！答案不正確（正確答案為 `{self.expected_answer}`），請重新點擊按鈕再試一次。",
                ephemeral=True
            )

        if self.is_test_only or not self.role_id:
            return await interaction.response.send_message(
                "✅ 測試驗證通過！您的答案正確。",
                ephemeral=True
            )

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "❌ 無法在私訊中發放伺服器身分組。",
                ephemeral=True
            )

        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message(
                "❌ 找不到指定的驗證身分組，請通知伺服器管理員重新設定。",
                ephemeral=True
            )

        if role in interaction.user.roles:
            return await interaction.response.send_message(
                f"✅ 您已經擁有 **{role.name}** 身分組，無需重複驗證！",
                ephemeral=True
            )

        try:
            await interaction.user.add_roles(role, reason="通過真人算術驗證")
            await interaction.response.send_message(
                f"🎉 驗證成功！已為您發放 **{role.name}** 身分組。",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ 機器人權限不足，無法發放該身分組（請確認機器人身分組順位高於目標身分組）。",
                ephemeral=True
            )
        except Exception as e:
            log.error(f"發放驗證身分組失敗: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ 發放身分組時發生錯誤：`{e}`",
                ephemeral=True
            )


# ==========================================
# 2. 永久驗證面板按鈕 View
# ==========================================
class VerifyPanelView(ui.View):
    def __init__(self, cog: "VerifyCog", mode: str, role_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.mode = mode
        self.role_id = role_id

        btn = ui.Button(
            label="🛡️ 點擊進行驗證",
            style=discord.ButtonStyle.success,
            custom_id=f"verify_btn:{mode}:{role_id}"
        )
        btn.callback = self.on_verify_click
        self.add_item(btn)

    async def on_verify_click(self, interaction: Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 請在伺服器頻道內進行驗證。", ephemeral=True)

        role = interaction.guild.get_role(self.role_id)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message(
                f"✅ 您已經擁有 **{role.name}** 身分組，無需重複驗證！",
                ephemeral=True
            )

        if self.mode == "math":
            await interaction.response.send_modal(MathVerifyModal(role_id=self.role_id, is_test_only=False))
        else:
            verify_url = self.cog.create_web_verify_session(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                role_id=self.role_id
            )
            link_view = ui.View()
            link_view.add_item(ui.Button(label="前往網頁完成驗證", style=discord.ButtonStyle.link, url=verify_url))
            await interaction.response.send_message(
                "🌐 請點擊下方按鈕前往驗證頁面完成授權（連結 10 分鐘內有效）：",
                view=link_view,
                ephemeral=True
            )


# ==========================================
# 3. 驗證系統核心 Cog
# ==========================================
class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.verify_cog = self
        # token -> {"guild_id": int, "user_id": int, "role_id": int, "expires_at": float}
        self.pending_web_verifications: dict[str, dict] = {}

    def _cleanup_expired_tokens(self):
        now = time.time()
        expired = [k for k, v in self.pending_web_verifications.items() if v.get("expires_at", 0) < now]
        for k in expired:
            self.pending_web_verifications.pop(k, None)

    def create_web_verify_session(self, guild_id: int, user_id: int, role_id: int) -> str:
        self._cleanup_expired_tokens()
        token = secrets.token_urlsafe(24)
        self.pending_web_verifications[token] = {
            "guild_id": guild_id,
            "user_id": user_id,
            "role_id": role_id,
            "expires_at": time.time() + 600
        }
        base_url = _get_base_web_url()
        return f"{base_url}/verify/{token}"

    async def grant_verified_role_by_token(self, token: str, oauth_user_id: str) -> tuple[bool, str]:
        self._cleanup_expired_tokens()
        session_data = self.pending_web_verifications.get(token)
        if not session_data:
            return False, "驗證連結已過期或無效，請回到 Discord 重新點擊驗證按鈕。"

        if str(session_data["user_id"]) != str(oauth_user_id):
            return False, "授權的 Discord 帳號與點擊驗證的成員不一致，請使用本人帳號驗證。"

        guild = self.bot.get_guild(session_data["guild_id"])
        if not guild:
            return False, "找不到對應的伺服器。"

        member = guild.get_member(session_data["user_id"])
        if not member:
            try:
                member = await guild.fetch_member(session_data["user_id"])
            except Exception:
                return False, "在伺服器中找不到您的成員資料。"

        role = guild.get_role(session_data["role_id"])
        if not role:
            return False, "找不到指定的驗證身分組，請通知管理員重新設定。"

        try:
            if role not in member.roles:
                await member.add_roles(role, reason="通過網頁驗證")
            self.pending_web_verifications.pop(token, None)
            return True, f"已成功在「{guild.name}」為您發放【{role.name}】身分組！"
        except discord.Forbidden:
            return False, "機器人權限不足，無法發放該身分組（請調整機器人身分組順位）。"
        except Exception as e:
            log.error(f"網頁驗證發放身分組錯誤: {e}", exc_info=True)
            return False, f"發放身分組時發生錯誤：{e}"

    @commands.Cog.listener()
    async def on_interaction(self, interaction: Interaction):
        """處理機器人重啟後的持久化驗證按鈕點擊。"""
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("verify_btn:"):
            return

        # 若該按鈕已由當次啟動的 View 處理則不重複回應
        if interaction.response.is_done():
            return

        parts = custom_id.split(":")
        if len(parts) != 3:
            return

        _, mode, role_id_str = parts
        if not role_id_str.isdigit():
            return

        role_id = int(role_id_str)
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 請在伺服器頻道內進行驗證。", ephemeral=True)

        role = interaction.guild.get_role(role_id)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message(
                f"✅ 您已經擁有 **{role.name}** 身分組，無需重複驗證！",
                ephemeral=True
            )

        if mode == "math":
            await interaction.response.send_modal(MathVerifyModal(role_id=role_id, is_test_only=False))
        else:
            verify_url = self.create_web_verify_session(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                role_id=role_id
            )
            link_view = ui.View()
            link_view.add_item(ui.Button(label="前往網頁完成驗證", style=discord.ButtonStyle.link, url=verify_url))
            await interaction.response.send_message(
                "🌐 請點擊下方按鈕前往驗證頁面完成授權（連結 10 分鐘內有效）：",
                view=link_view,
                ephemeral=True
            )

    @app_commands.command(name="setup_verify", description="建立伺服器驗證面板（僅限管理員）")
    @app_commands.guild_only()
    @app_commands.describe(
        mode="驗證模式",
        role="通過驗證後要發放的身分組",
        title="驗證面板標題（選填）",
        description="驗證面板說明文字（選填）"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="🧮 一般驗證", value="math"),
        app_commands.Choice(name="🌐 網頁驗證", value="web")
    ])
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verify(
        self,
        interaction: Interaction,
        mode: app_commands.Choice[str],
        role: discord.Role,
        title: str = "🛡️ 伺服器真人驗證",
        description: str = "請點擊下方按鈕完成驗證，通過後即可自動獲得完整瀏覽權限。"
    ):
        # 雙重檢查：確保執行者具備伺服器管理員權限
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ 權限不足！只有擁有「管理員 (Administrator)」權限的成員才能建立驗證面板。",
                ephemeral=True
            )

        bot_member = interaction.guild.me
        if not bot_member.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                "❌ 機器人缺少「管理身分組 (Manage Roles)」權限，請先到伺服器設定開啟權限。",
                ephemeral=True
            )

        if role.is_default() or role.managed:
            return await interaction.response.send_message(
                "❌ 無法使用 `@everyone` 或系統託管身分組作為驗證身分組。",
                ephemeral=True
            )

        if role >= bot_member.top_role:
            return await interaction.response.send_message(
                f"❌ 機器人的最高身分組順位低於或等於 **{role.name}**，請在伺服器設定中將機器人身分組往上拉。",
                ephemeral=True
            )

        selected_mode = mode.value
        mode_label = "🧮 一般驗證" if selected_mode == "math" else "🌐 網頁驗證"

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green()
        )
        embed.add_field(name="驗證方式", value=mode_label, inline=True)
        embed.add_field(name="驗證身分組", value=role.mention, inline=True)

        view = VerifyPanelView(self, mode=selected_mode, role_id=role.id)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            f"✅ 已成功建立驗證面板（模式：**{mode_label}**，發放身分組：{role.mention}）！",
            ephemeral=True
        )

    @setup_verify.error
    async def setup_verify_error(self, interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "❌ 權限不足！只有伺服器管理員才能使用 `/setup_verify`。"
        else:
            log.error(f"/setup_verify 執行錯誤: {error}", exc_info=True)
            msg = f"❌ 建立驗證面板時發生錯誤：{error}"

        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="verify", description="測試真人驗證彈窗（不會發放任何身分組）")
    async def verify_test(self, interaction: Interaction):
        await interaction.response.send_modal(MathVerifyModal(role_id=None, is_test_only=True))


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))