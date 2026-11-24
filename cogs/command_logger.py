import discord
from discord import app_commands, Interaction
from discord.ext import commands
from datetime import datetime
import os
import re
import logging

log = logging.getLogger("CommandLogger")

# 全域共用的指令紀錄列表（最多保留 200 筆）
COMMAND_LOGS: list[dict] = []

# 💬 請填寫「只有你看得到」的私人紀錄頻道 ID（也可在環境變數設定 COMMAND_LOG_CHANNEL_ID）
DEFAULT_ADMIN_LOG_CHANNEL_ID = 1574786199669833860

# 💬 在此頻道輸入「訊息 ID」，機器人會自動查詢是誰發送的
DEFAULT_MESSAGE_LOOKUP_CHANNEL_ID = 1574796011346468894

# 自動記錄機器人看過的訊息索引（最多保留最新 5000 筆，避免記憶體無限膨脹）
# 💬 在此頻道輸入「ID」，機器人會到機器人指令日誌裡翻找是否有該 ID
DEFAULT_MESSAGE_LOOKUP_CHANNEL_ID = 1574796011346468894


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
            log.warning(f"無效的 COMMAND_LOG_CHANNEL_ID 設定: {env_channel_id}，改用預設值 {DEFAULT_ADMIN_LOG_CHANNEL_ID}")
            self.admin_log_channel_id = DEFAULT_ADMIN_LOG_CHANNEL_ID

        env_lookup_channel_id = (os.getenv("MESSAGE_LOOKUP_CHANNEL_ID") or "").strip()
        try:
            self.lookup_channel_id = int(env_lookup_channel_id) if env_lookup_channel_id else DEFAULT_MESSAGE_LOOKUP_CHANNEL_ID
        except ValueError:
            log.warning(f"無效的 MESSAGE_LOOKUP_CHANNEL_ID 設定: {env_lookup_channel_id}，改用預設值 {DEFAULT_MESSAGE_LOOKUP_CHANNEL_ID}")
            self.lookup_channel_id = DEFAULT_MESSAGE_LOOKUP_CHANNEL_ID

    def _get_now_str(self) -> str:
        """取得目前時間字串，優先使用專案內的 safe_now。"""
        try:
            from utils.time_utils import safe_now
            val = safe_now()
            if val:
                return str(val)
        except Exception:
            pass
        return _default_safe_now()

    def _format_options(self, interaction: Interaction) -> str:
        """整理斜線指令輸入參數為易讀文字。"""
        try:
            options = getattr(interaction.namespace, "__dict__", {}) or {}
            items = []
            for k, v in options.items():
                if v is not None:
                    items.append(f"{k}: {v}")
            return ", ".join(items) if items else "無參數"
        except Exception:
            return "無參數"

    def _get_log_viewer_ids(self) -> tuple[set[int], set[int]]:
        """從 bot 屬性或主模組取得可查看日誌的使用者 ID 集合。"""
        special_ids: set[int] = set()
        viewer_ids: set[int] = set()
        for attr_name, target_set in (("SPECIAL_USER_IDS", special_ids), ("LOG_VIEWER_IDS", viewer_ids)):
            raw = getattr(self.bot, attr_name, None)
            if isinstance(raw, (set, list, tuple)):
                for item in raw:
                    try:
                        target_set.add(int(item))
                    except (TypeError, ValueError):
                        pass
        return special_ids, viewer_ids

    def _append_command_log(
        self,
        text: str,
        time_str: str,
        ref_ids: list[str] | None = None,
        details: dict | None = None
    ):
        entry = {
            "time": time_str,
            "text": text,
            "ids": [str(x) for x in (ref_ids or []) if x],
            "details": details or {}
        }
        COMMAND_LOGS.append(entry)
        if len(COMMAND_LOGS) > 200:
            COMMAND_LOGS.pop(0)

        bot_logs = getattr(self.bot, "command_logs", None)
        if isinstance(bot_logs, list) and bot_logs is not COMMAND_LOGS:
            bot_logs.append(entry)
            if len(bot_logs) > 200:
                bot_logs.pop(0)

    def _get_all_command_logs(self) -> list[dict]:
        """取得機器人目前保存的指令日誌列表。"""
        bot_logs = getattr(self.bot, "command_logs", None)
        if isinstance(bot_logs, list) and bot_logs:
            return bot_logs
        return COMMAND_LOGS

    def _find_log_by_id(self, target_id: str) -> dict | None:
        """在機器人指令日誌中翻找是否包含指定的 ID。"""
        clean_id = str(target_id).strip()
        if not clean_id:
            return None

        logs = self._get_all_command_logs()
        for entry in reversed(logs):
            if not isinstance(entry, dict):
                continue
            entry_ids = [str(x) for x in (entry.get("ids") or [])]
            entry_text = str(entry.get("text") or "")
            details = entry.get("details") or {}
            detail_values = " ".join(str(v) for v in details.values() if v is not None)
            if (
                clean_id in entry_ids
                or re.search(rf"(?<!\d){re.escape(clean_id)}(?!\d)", entry_text)
                or re.search(rf"(?<!\d){re.escape(clean_id)}(?!\d)", detail_values)
            ):
                return entry
        return None

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

            ref_ids = [str(interaction.id)]
            if interaction.message and getattr(interaction.message, "id", None):
                ref_ids.append(str(interaction.message.id))
            if channel and getattr(channel, "last_message_id", None):
                ref_ids.append(str(channel.last_message_id))

            summary_text = (
                f"{user} ({user.id}) 在 {guild_name}({guild_id}) 執行 {cmd_name} "
                f"({params_text}) [ID: {', '.join(ref_ids)}]"
            )
            details = {
                "executor_mention": user.mention,
                "executor_name": f"{user.display_name} ({user.name})",
                "executor_id": str(user.id),
                "command": cmd_name,
                "guild_text": guild_text,
                "channel_text": channel_text,
                "params_text": params_text,
                "avatar_url": str(user.display_avatar.url),
            }
            self._append_command_log(summary_text, now_str, ref_ids=ref_ids, details=details)

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

    async def _find_message_by_id(self, message_id: int, hint_channel: discord.abc.Messageable | None = None):
        """依序在提示頻道、機器人快取文字頻道與各伺服器頻道中尋找指定訊息 ID。"""
        candidate_channels = []

        if isinstance(hint_channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel)):
            candidate_channels.append(hint_channel)

        for guild in self.bot.guilds:
            for ch in guild.text_channels:
                if ch not in candidate_channels:
                    candidate_channels.append(ch)
            for th in getattr(guild, "threads", []):
                if th not in candidate_channels:
                    candidate_channels.append(th)

        for ch in candidate_channels:
            perms = ch.permissions_for(ch.guild.me) if hasattr(ch, "guild") and ch.guild and ch.guild.me else None
            if perms and not (perms.read_message_history and perms.view_channel):
                continue
            try:
                msg = await ch.fetch_message(message_id)
                if msg:
                    return msg
            except discord.NotFound:
                # 該頻道內無此訊息（HTTP 404 / error code 10008），繼續搜尋下一個頻道
                continue
            except (discord.Forbidden, discord.HTTPException):
                continue

        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """在指定頻道輸入 ID 時，到機器人指令日誌裡翻找並搬出資料；查不到則回覆查詢不到此id。"""
        try:
            if message.author.bot:
                return

            if message.channel.id != self.lookup_channel_id:
                return

            raw_content = (message.content or "").strip()
            id_match = re.fullmatch(r"\d{15,22}", raw_content)
            if not id_match:
                return

            target_id = id_match.group(0)
            matched_log = self._find_log_by_id(target_id)

            if not matched_log:
                await message.reply("查詢不到此id", mention_author=False)
                return

            log_time = matched_log.get("time", "未知時間")
            log_text = matched_log.get("text", "")
            log_ids = matched_log.get("ids") or []
            details = matched_log.get("details") or {}

            executor_mention = details.get("executor_mention") or details.get("executor_name") or "未知執行者"
            executor_id = details.get("executor_id") or "未知 ID"
            command_name = details.get("command") or "未知指令"
            guild_text = details.get("guild_text") or "未知伺服器"
            channel_text = details.get("channel_text") or "未知頻道"
            params_text = details.get("params_text") or "無參數"
            ids_display = ", ".join(f"`{x}`" for x in log_ids) if log_ids else f"`{target_id}`"

            embed = discord.Embed(
                title="📄 機器人指令日誌查詢結果",
                description=f"```\n{log_text}\n```" if log_text else "（無摘要內容）",
                color=discord.Color.blurple()
            )
            if details.get("executor_name"):
                embed.set_author(name=details["executor_name"], icon_url=details.get("avatar_url") or None)

            embed.add_field(name="👤 執行者", value=f"{executor_mention}\n`{executor_id}`", inline=True)
            embed.add_field(name="⌨️ 使用指令", value=f"**`{command_name}`**", inline=True)
            embed.add_field(name="🏠 所在伺服器", value=guild_text, inline=True)
            embed.add_field(name="📍 所在頻道", value=channel_text, inline=True)
            embed.add_field(name="⚙️ 輸入參數", value=str(params_text)[:1000], inline=False)
            embed.add_field(name="🕒 紀錄時間", value=f"`{log_time}`", inline=True)
            embed.add_field(name="🆔 關聯 ID", value=ids_display[:1000], inline=True)

            await message.reply(embed=embed, mention_author=False)
        except Exception as e:
            log.error(f"翻查機器人指令日誌時發生錯誤: {e}", exc_info=True)
            try:
                await message.reply("查詢不到此id", mention_author=False)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandLoggerCog(bot))