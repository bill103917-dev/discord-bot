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
MESSAGE_INDEX: dict[int, dict] = {}
MAX_MESSAGE_INDEX = 5000


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

        env_lookup_id = (os.getenv("MESSAGE_LOOKUP_CHANNEL_ID") or "").strip()
        try:
            self.lookup_channel_id = int(env_lookup_id) if env_lookup_id else DEFAULT_MESSAGE_LOOKUP_CHANNEL_ID
        except ValueError:
            self.lookup_channel_id = DEFAULT_MESSAGE_LOOKUP_CHANNEL_ID

        if not hasattr(self.bot, "message_index") or not isinstance(self.bot.message_index, dict):
            self.bot.message_index = MESSAGE_INDEX

    def _record_message_index(self, message: discord.Message):
        """記錄看過的訊息 ID 與發送者/頻道資訊，方便日後直接以訊息 ID 查詢。"""
        if not message or not message.id or message.author.bot:
            return

        author = message.author
        guild = message.guild
        channel = message.channel

        content_preview = (message.content or "").strip()
        if not content_preview and message.attachments:
            content_preview = f"[附件：{', '.join(a.filename for a in message.attachments)}]"
        elif not content_preview and message.embeds:
            content_preview = "[內嵌卡片 Embed]"
        elif not content_preview:
            content_preview = "[無純文字內容]"

        record = {
            "message_id": message.id,
            "author_id": author.id,
            "author_mention": author.mention,
            "author_display_name": author.display_name,
            "author_name": author.name,
            "author_avatar": author.display_avatar.url,
            "guild_id": guild.id if guild else None,
            "guild_name": guild.name if guild else None,
            "channel_id": channel.id if hasattr(channel, "id") else None,
            "channel_mention": channel.mention if hasattr(channel, "mention") else None,
            "created_at": message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "content": content_preview[:1000],
            "jump_url": getattr(message, "jump_url", None),
        }

        MESSAGE_INDEX[message.id] = record
        if isinstance(self.bot.message_index, dict) and self.bot.message_index is not MESSAGE_INDEX:
            self.bot.message_index[message.id] = record

        while len(MESSAGE_INDEX) > MAX_MESSAGE_INDEX:
            oldest_key = next(iter(MESSAGE_INDEX))
            MESSAGE_INDEX.pop(oldest_key, None)

        if isinstance(self.bot.message_index, dict):
            while len(self.bot.message_index) > MAX_MESSAGE_INDEX:
                oldest_key = next(iter(self.bot.message_index))
                self.bot.message_index.pop(oldest_key, None)

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
        """自動索引看過的訊息；若在指定的私人查詢頻道輸入訊息 ID，則查詢發送者。"""
        try:
            if message.author.bot:
                return

            # 只要不是在查詢頻道輸入純 ID/連結的查詢指令，就順手把這則訊息記進索引
            if message.channel.id != self.lookup_channel_id:
                self._record_message_index(message)
                return

            raw_content = (message.content or "").strip()
            # 支援直接貼純數字 ID，或貼上訊息連結（如 https://discord.com/channels/guild/chan/msg_id）
            link_match = re.search(r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)", raw_content)
            if link_match:
                target_channel_id = int(link_match.group(1))
                target_message_id = int(link_match.group(2))
            else:
                id_match = re.fullmatch(r"\d{15,22}", raw_content)
                if not id_match:
                    self._record_message_index(message)
                    return
                target_channel_id = None
                target_message_id = int(id_match.group(0))

            status_msg = await message.reply(f"🔍 正在搜尋訊息 ID `{target_message_id}` 的發送者...", mention_author=False)

            # 1. 優先從已記錄的訊息索引快取查詢
            cached = MESSAGE_INDEX.get(target_message_id)
            if not cached and isinstance(self.bot.message_index, dict):
                cached = self.bot.message_index.get(target_message_id)

            if cached:
                guild_info = f"{cached['guild_name']} (`{cached['guild_id']}`)" if cached.get("guild_id") else "私訊 (DM)"
                channel_info = (
                    f"{cached['channel_mention']} (`{cached['channel_id']}`)"
                    if cached.get("channel_mention")
                    else f"頻道 ID: `{cached.get('channel_id')}`"
                )
                embed = discord.Embed(
                    title="🔎 訊息發送者查詢結果（索引紀錄）",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow()
                )
                embed.set_author(
                    name=f"{cached['author_display_name']} ({cached['author_name']})",
                    icon_url=cached["author_avatar"]
                )
                embed.add_field(name="👤 發送者", value=f"{cached['author_mention']}\n`{cached['author_id']}`", inline=True)
                embed.add_field(name="🆔 訊息 ID", value=f"`{cached['message_id']}`", inline=True)
                embed.add_field(name="🏠 所在伺服器", value=guild_info, inline=False)
                embed.add_field(name="📍 所在頻道", value=channel_info, inline=False)
                embed.add_field(name="🕒 發送時間", value=f"`{cached['created_at']}`", inline=False)
                embed.add_field(name="💬 訊息內容", value=cached["content"][:1000], inline=False)
                if cached.get("jump_url"):
                    embed.add_field(name="🔗 跳轉連結", value=f"[點我前往該則訊息]({cached['jump_url']})", inline=False)

                await status_msg.edit(content=None, embed=embed)
                return

            # 2. 索引中沒有時，再向指定頻道或各頻道撈取
            found_msg = None
            if target_channel_id:
                ch = self.bot.get_channel(target_channel_id)
                if not ch:
                    try:
                        ch = await self.bot.fetch_channel(target_channel_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        ch = None
                if ch and hasattr(ch, "fetch_message"):
                    try:
                        found_msg = await ch.fetch_message(target_message_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        found_msg = None

            if not found_msg:
                found_msg = await self._find_message_by_id(target_message_id)

            if not found_msg:
                await status_msg.edit(
                    content=(
                        f"❌ 找不到訊息 ID `{target_message_id}`。\n"
                        "可能原因：該則訊息是在機器人啟動/記錄索引前發送的、已被刪除，或不在機器人可查看的頻道內。"
                    )
                )
                return

            self._record_message_index(found_msg)

            author = found_msg.author
            guild_info = f"{found_msg.guild.name} (`{found_msg.guild.id}`)" if found_msg.guild else "私訊 (DM)"
            channel_info = (
                f"{found_msg.channel.mention} (`{found_msg.channel.id}`)"
                if hasattr(found_msg.channel, "mention")
                else f"頻道 ID: `{found_msg.channel.id}`"
            )
            created_str = found_msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            content_preview = (found_msg.content or "").strip()
            if not content_preview and found_msg.attachments:
                content_preview = f"[附件：{', '.join(a.filename for a in found_msg.attachments)}]"
            elif not content_preview and found_msg.embeds:
                content_preview = "[內嵌卡片 Embed]"
            elif not content_preview:
                content_preview = "[無純文字內容]"

            embed = discord.Embed(
                title="🔎 訊息發送者查詢結果",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(
                name=f"{author.display_name} ({author.name})",
                icon_url=author.display_avatar.url
            )
            embed.add_field(name="👤 發送者", value=f"{author.mention}\n`{author.id}`", inline=True)
            embed.add_field(name="🆔 訊息 ID", value=f"`{found_msg.id}`", inline=True)
            embed.add_field(name="🏠 所在伺服器", value=guild_info, inline=False)
            embed.add_field(name="📍 所在頻道", value=channel_info, inline=False)
            embed.add_field(name="🕒 發送時間", value=f"`{created_str}`", inline=False)
            embed.add_field(name="💬 訊息內容", value=content_preview[:1000], inline=False)
            if getattr(found_msg, "jump_url", None):
                embed.add_field(name="🔗 跳轉連結", value=f"[點我前往該則訊息]({found_msg.jump_url})", inline=False)

            await status_msg.edit(content=None, embed=embed)
        except Exception as e:
            log.error(f"查詢訊息 ID 時發生錯誤: {e}", exc_info=True)
            try:
                await message.reply(f"❌ 查詢時發生錯誤：`{e}`", mention_author=False)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandLoggerCog(bot))