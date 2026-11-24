import discord
from discord import app_commands, Interaction
from discord.ext import commands
from datetime import datetime
import os
import logging

log = logging.getLogger("CommandLogger")

# 全域共用的指令紀錄列表（最多保留 200 筆）
COMMAND_LOGS: list[dict] = []

# 💬 請填寫「只有你看得到」的私人紀錄頻道 ID（也可在環境變數設定 COMMAND_LOG_CHANNEL_ID）
DEFAULT_ADMIN_LOG_CHANNEL_ID = 1574786199669833860


def _default_safe_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CommandLoggerCog(commands.Cog):
    """
    全域斜線指令 (/) 自動監聽與日誌管理 Cog：
    1. 透過 on_app_command_completion 自動捕捉所有 / 指令執行。
    2. 同時同步寫入 bot.command_logs 與模組層級 COMMAND_LOGS，避免不同 import 路徑造成分家。
    3. 僅發送至管理員指定的私人頻道，不會推送到其他伺服器或使用者。
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not hasattr(self.bot, "command_logs") or not isinstance(self.bot.command_logs, list):
            self.bot.command_logs = COMMAND_LOGS

        env_channel_id = (os.getenv("COMMAND_LOG_CHANNEL_ID") or "").strip()
        try:
            self.admin_log_channel_id = int(env_channel_id) if env_channel_id else DEFAULT_ADMIN_LOG_CHANNEL_ID
        except ValueError:
            self.admin_log_channel_id = DEFAULT_ADMIN_LOG_CHANNEL_ID

    def _get_now_str(self) -> str:
        safe_now_fn = getattr(self.bot, "safe_now", None) or globals().get("safe_now")
        if callable(safe_now_fn):
            try:
                return str(safe_now_fn())
            except Exception:
                pass
        return _default_safe_now()

    def _get_log_viewer_ids(self) -> tuple[set[int], set[int]]:
        special_ids = set(getattr(self.bot, "special_user_ids", ()) or ())
        viewer_ids = set(getattr(self.bot, "log_viewer_ids", ()) or ())
        return special_ids, viewer_ids

    def _format_options(self, interaction: Interaction) -> str:
        options = interaction.data.get("options", []) if interaction.data else []
        if not options:
            return "無參數"

        parsed: list[str] = []

        def extract_opts(opt_list: list[dict]):
            for opt in opt_list:
                if "options" in opt:
                    extract_opts(opt["options"])
                elif "value" in opt:
                    parsed.append(f"`{opt['name']}`: `{opt['value']}`")

        extract_opts(options)
        return "、".join(parsed) if parsed else "無參數"

    def _append_command_log(self, text: str, time_str: str):
        entry = {"time": time_str, "text": text}
        COMMAND_LOGS.append(entry)
        if len(COMMAND_LOGS) > 200:
            COMMAND_LOGS.pop(0)

        bot_logs = getattr(self.bot, "command_logs", None)
        if isinstance(bot_logs, list) and bot_logs is not COMMAND_LOGS:
            bot_logs.append(entry)
            if len(bot_logs) > 200:
                bot_logs.pop(0)

    async def _send_to_admin_channel(self, embed: discord.Embed):
        if not self.admin_log_channel_id:
            return

        channel = self.bot.get_channel(self.admin_log_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(self.admin_log_channel_id)
            except discord.Forbidden:
                log.warning(f"無權限讀取管理員紀錄頻道 {self.admin_log_channel_id}")
                return
            except Exception as e:
                log.error(f"取得管理員紀錄頻道失敗: {e}")
                return

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning(f"無權限發送指令紀錄至頻道 {self.admin_log_channel_id}")
        except Exception as e:
            log.error(f"發送指令紀錄至私人頻道失敗: {e}")

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: Interaction,
        command: app_commands.Command | app_commands.ContextMenu
    ):
        try:
            if interaction.client.user and self.bot.user and interaction.client.user.id != self.bot.user.id:
                return

            user = interaction.user
            guild = interaction.guild
            channel = interaction.channel
            cmd_name = f"/{command.qualified_name}"
            params_text = self._format_options(interaction)
            now_str = self._get_now_str()

            guild_name = guild.name if guild else "DM"
            guild_id = guild.id if guild else None
            guild_text = f"{guild_name} (`{guild_id}`)" if guild else "私訊 (DM)"
            channel_text = f"{channel.mention} (`{channel.id}`)" if hasattr(channel, "mention") else "私訊 (DM)"

            summary_text = f"{user} 在 {guild_name}({guild_id}) 執行 {cmd_name} ({params_text})"
            self._append_command_log(summary_text, now_str)

            log.info(f"[指令紀錄] {user} ({user.id}) @ {guild_text} {channel_text} -> {cmd_name} | {params_text}")
            print(f"📜 [{now_str}] [指令執行] {user.display_name} ({user.id}) -> {cmd_name} ({params_text})")

            embed = discord.Embed(
                title="📜 指令使用紀錄（管理員專用）",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(
                name=f"{user.display_name} ({user.name})",
                icon_url=user.display_avatar.url
            )
            embed.add_field(name="👤 執行者", value=f"{user.mention}\n`{user.id}`", inline=True)
            embed.add_field(name="⌨️ 使用指令", value=f"**`{cmd_name}`**", inline=True)
            embed.add_field(name="🏠 所在伺服器", value=guild_text, inline=True)
            embed.add_field(name="📍 所在頻道", value=channel_text, inline=True)
            embed.add_field(name="⚙️ 輸入參數", value=params_text[:1000], inline=False)

            await self._send_to_admin_channel(embed)
        except Exception as e:
            log.error(f"記錄斜線指令時發生未預期錯誤: {e}", exc_info=True)

    @app_commands.command(name="logs", description="在 Discord 訊息中顯示最近的指令紀錄")
    async def logs(self, interaction: Interaction):
        user_id = int(interaction.user.id)
        special_ids, viewer_ids = self._get_log_viewer_ids()

        if special_ids or viewer_ids:
            if user_id not in special_ids and user_id not in viewer_ids:
                try:
                    await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
                except Exception:
                    pass
                return
        elif not interaction.user.guild_permissions.administrator:
            try:
                await interaction.response.send_message("❌ 你沒有權限使用此指令", ephemeral=True)
            except Exception:
                pass
            return

        logs_source = getattr(self.bot, "command_logs", None)
        if not isinstance(logs_source, list) or not logs_source:
            logs_source = COMMAND_LOGS

        logs_text = "📜 **最近的指令紀錄**\n\n"
        if not logs_source:
            logs_text += "目前沒有任何紀錄。"
        else:
            logs_text += "\n".join([f"`{entry['time']}`: {entry['text']}" for entry in logs_source[-10:]])

        try:
            await interaction.response.send_message(logs_text, ephemeral=True)
        except Exception:
            try:
                await interaction.followup.send(logs_text, ephemeral=True)
            except Exception:
                log.warning("Logs: 無法回應 /logs 指令")


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandLoggerCog(bot))