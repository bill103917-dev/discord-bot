import discord
from discord import app_commands, Interaction, ui
from discord.ext import commands
import random
import os
import re
import time
import json
import asyncio
import logging

log = logging.getLogger("VerifyCog")

VERIFY_STORAGE_FILE = "verify_panels_config.json"


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
# 🔘 一般模式按鈕視圖 (支援持久化與重啟後自動解析身分組)
# ==========================================
class MathVerifyButtonView(ui.View):
    def __init__(self, cog=None, role_id: int = 0):
        super().__init__(timeout=None)
        self.cog = cog
        self.role_id = role_id

    def _resolve_role_id(self, interaction: Interaction) -> int:
        if self.role_id:
            return self.role_id
        if self.cog and interaction.message:
            return self.cog.resolve_and_recover_role_id(interaction.message) or 0
        return 0

    @ui.button(label="🛡️ 點擊進行驗證", style=discord.ButtonStyle.success, custom_id="btn_math_verify")
    async def start_math_verify(self, interaction: Interaction, button: ui.Button):
        target_role_id = self._resolve_role_id(interaction)
        if not target_role_id:
            return await interaction.response.send_message(
                "❌ 找不到此驗證面板的身分組設定，請聯繫管理員重新建立面板。",
                ephemeral=True
            )

        role = interaction.guild.get_role(target_role_id)
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

        modal = VerifyModal(expected_answer=str(ans), role_id=target_role_id, question_text=question)
        await interaction.response.send_modal(modal)


# ==========================================
# 🌐 網頁認證模式按鈕視圖 (支援持久化與重啟後自動解析身分組)
# ==========================================
class WebVerifyButtonView(ui.View):
    def __init__(self, cog=None, guild_id: int = 0, role_id: int = 0):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.role_id = role_id

    def _resolve_target(self, interaction: Interaction) -> tuple[int, int]:
        guild_id = self.guild_id or (interaction.guild_id or 0)
        role_id = self.role_id
        if not role_id and self.cog and interaction.message:
            role_id = self.cog.resolve_and_recover_role_id(interaction.message) or 0
        return guild_id, role_id

    @ui.button(label="🌐 前往網頁認證", style=discord.ButtonStyle.primary, custom_id="btn_web_verify_open")
    async def open_web_verify(self, interaction: Interaction, button: ui.Button):
        guild_id, target_role_id = self._resolve_target(interaction)
        if not target_role_id:
            return await interaction.response.send_message(
                "❌ 找不到此驗證面板的身分組設定，請聯繫管理員重新建立面板。",
                ephemeral=True
            )

        role = interaction.guild.get_role(target_role_id)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message(
                f"✅ 您已經擁有 {role.mention} 身分組，無需重複驗證。",
                ephemeral=True
            )

        if not self.cog:
            return await interaction.response.send_message(
                "❌ 驗證系統尚未初始化完成，請稍後再試。",
                ephemeral=True
            )

        base_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000").rstrip("/")
        token = f"{interaction.user.id}_{guild_id}_{target_role_id}_{random.randint(100000, 999999)}"
        self.cog.pending_web_verifications[token] = {
            "user_id": interaction.user.id,
            "guild_id": guild_id,
            "role_id": target_role_id,
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
        # 持久化面板設定：{str(message_id): {"guild_id": int, "channel_id": int, "role_id": int, "mode": str}}
        self.panels_config = self._load_panels_config()
        self._persistent_views_registered = False
        bot.verify_cog = self

    def _load_panels_config(self) -> dict:
        if not os.path.exists(VERIFY_STORAGE_FILE):
            return {}
        try:
            with open(VERIFY_STORAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            log.error(f"讀取驗證面板持久化設定失敗: {e}")
            return {}

    def _save_panels_config(self) -> None:
        try:
            with open(VERIFY_STORAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.panels_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"儲存驗證面板持久化設定失敗: {e}")

    def register_panel_config(self, message_id: int, guild_id: int, channel_id: int, role_id: int, mode: str) -> None:
        self.panels_config[str(message_id)] = {
            "guild_id": int(guild_id),
            "channel_id": int(channel_id),
            "role_id": int(role_id),
            "mode": mode
        }
        self._save_panels_config()

    def get_panel_role_id(self, message_id: int) -> int | None:
        info = self.panels_config.get(str(message_id))
        if info and "role_id" in info:
            return int(info["role_id"])
        return None

    def resolve_and_recover_role_id(self, message: discord.Message) -> int | None:
        """先查持久化設定；若重啟後遺失設定，則從面板 Embed 還原身分組 ID 並回寫存檔。"""
        cached_role_id = self.get_panel_role_id(message.id)
        if cached_role_id:
            return cached_role_id

        guild = message.guild
        if not guild or not message.embeds:
            return None

        for embed in message.embeds:
            text_blobs = [
                embed.description or "",
                embed.title or "",
                embed.footer.text if embed.footer and embed.footer.text else "",
            ]
            for field in embed.fields:
                text_blobs.append(field.name or "")
                text_blobs.append(field.value or "")

            combined_text = "\n".join(text_blobs)

            # 1. 優先匹配 Discord 身分組提及格式 <@&123456789012345678>
            mention_matches = re.findall(r"<@&(\d{15,22})>", combined_text)
            for rid_str in mention_matches:
                rid = int(rid_str)
                if guild.get_role(rid):
                    mode = "web" if any(
                        isinstance(comp, discord.ui.Button) and comp.custom_id == "btn_web_verify_open"
                        for row in message.components
                        for comp in getattr(row, "children", [])
                    ) else "math"
                    self.register_panel_config(
                        message_id=message.id,
                        guild_id=guild.id,
                        channel_id=message.channel.id,
                        role_id=rid,
                        mode=mode
                    )
                    return rid

            # 2. 次要匹配純數字身分組 ID（排除 message.id 與 channel.id）
            id_matches = re.findall(r"\b(\d{15,22})\b", combined_text)
            for rid_str in id_matches:
                rid = int(rid_str)
                if rid not in (message.id, message.channel.id) and guild.get_role(rid):
                    mode = "web" if any(
                        isinstance(comp, discord.ui.Button) and comp.custom_id == "btn_web_verify_open"
                        for row in message.components
                        for comp in getattr(row, "children", [])
                    ) else "math"
                    self.register_panel_config(
                        message_id=message.id,
                        guild_id=guild.id,
                        channel_id=message.channel.id,
                        role_id=rid,
                        mode=mode
                    )
                    return rid

        return None

    async def cog_load(self):
        # 註冊永久 View，確保重啟後舊按鈕不會出現「應用程式未即時回應」
        if not self._persistent_views_registered:
            self.bot.add_view(MathVerifyButtonView(cog=self))
            self.bot.add_view(WebVerifyButtonView(cog=self))
            self._persistent_views_registered = True
        asyncio.create_task(self._restore_panel_views_on_ready())

    async def _restore_panel_views_on_ready(self):
        await self.bot.wait_until_ready()
        for msg_id_str, info in list(self.panels_config.items()):
            try:
                msg_id = int(msg_id_str)
                channel_id = int(info.get("channel_id", 0))
                role_id = int(info.get("role_id", 0))
                mode = info.get("mode", "math")

                channel = self.bot.get_channel(channel_id)
                if not channel and channel_id:
                    channel = await self.bot.fetch_channel(channel_id)
                if not channel:
                    continue

                partial_msg = channel.get_partial_message(msg_id)
                view = MathVerifyButtonView(cog=self, role_id=role_id) if mode == "math" else WebVerifyButtonView(
                    cog=self, guild_id=int(info.get("guild_id", 0)), role_id=role_id
                )
                await partial_msg.edit(view=view)
            except Exception as e:
                log.warning(f"重新掛載驗證面板 {msg_id_str} 狀態略過: {e}")

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
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verify(
        self,
        interaction: Interaction,
        role: discord.Role,
        mode: app_commands.Choice[str],
        title: str = "🛡️ 伺服器身分驗證",
        description: str = "請點擊下方按鈕完成驗證，通過後系統將自動發放身分組。"
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ 權限不足：只有伺服器「管理員（Administrator）」才能建立驗證面板。",
                ephemeral=True
            )

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
            view = MathVerifyButtonView(cog=self, role_id=role.id)
        else:
            view = WebVerifyButtonView(cog=self, guild_id=interaction.guild_id, role_id=role.id)

        sent_msg = await interaction.channel.send(embed=embed, view=view)
        self.register_panel_config(
            message_id=sent_msg.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            role_id=role.id,
            mode=selected_mode
        )
        await interaction.response.send_message(
            f"✅ 已在 {interaction.channel.mention} 建立驗證面板（模式：{mode_label}），並已啟用重啟持久化。",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))