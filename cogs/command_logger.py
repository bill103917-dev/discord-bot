import discord
from discord import app_commands, Interaction
from discord.ext import commands
from datetime import datetime
import logging

log = logging.getLogger("CommandLogger")


class CommandLoggerCog(commands.Cog):
    """
    全域斜線指令 (/) 自動監聽器：
    只會捕捉「你這隻機器人」自己的斜線指令，完全不會收到其他機器人的指令。
    無需在任何指令內額外加入紀錄程式碼。
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _format_options(self, interaction: Interaction) -> str:
        """自動解析並整理使用者在斜線指令中輸入的所有參數"""
        options = interaction.data.get("options", []) if interaction.data else []
        if not options:
            return "無參數"

        parsed = []

        def extract_opts(opt_list):
            for opt in opt_list:
                if "options" in opt:
                    extract_opts(opt["options"])
                elif "value" in opt:
                    parsed.append(f"`{opt['name']}`: `{opt['value']}`")

        extract_opts(options)
        return "、".join(parsed) if parsed else "無參數"

    async def _dispatch_to_log_channel(self, guild: discord.Guild, embed: discord.Embed):
        """自動尋找該伺服器設定的 Log 頻道並發送紀錄"""
        if not guild:
            return

        log_channel = None
        logs_cog = self.bot.get_cog("LogsCog")

        if logs_cog:
            for attr_name in ("log_channels", "guild_logs", "config", "log_config", "settings"):
                mapping = getattr(logs_cog, attr_name, None)
                if isinstance(mapping, dict) and guild.id in mapping:
                    val = mapping[guild.id]
                    channel_id = val[0] if isinstance(val, (list, tuple)) else (
                        val.get("channel_id") if isinstance(val, dict) else val
                    )
                    if channel_id:
                        log_channel = guild.get_channel(int(channel_id))
                        break

        if not log_channel:
            for ch in guild.text_channels:
                if any(k in ch.name.lower() for k in ("bot-log", "command-log", "日誌", "指令紀錄")):
                    log_channel = ch
                    break

        if log_channel:
            try:
                await log_channel.send(embed=embed)
            except discord.Forbidden:
                log.warning(f"無權限發送指令紀錄至 {guild.name} 的 #{log_channel.name}")
            except Exception as e:
                log.error(f"發送指令紀錄失敗: {e}")

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: Interaction,
        command: app_commands.Command | app_commands.ContextMenu
    ):
        # 雙重保險：確保指令確實屬於本機器人的指令樹
        if interaction.client.user and self.bot.user and interaction.client.user.id != self.bot.user.id:
            return

        user = interaction.user
        guild = interaction.guild
        channel = interaction.channel
        cmd_name = f"/{command.qualified_name}"
        params_text = self._format_options(interaction)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        guild_label = f"{guild.name} ({guild.id})" if guild else "私訊 (DM)"
        channel_label = f"#{channel.name}" if hasattr(channel, "name") else "DM"
        log.info(f"[指令紀錄] {user} ({user.id}) 在 {guild_label} {channel_label} 執行了 {cmd_name} | 參數: {params_text}")
        print(f"📜 [{now_str}] [指令執行] {user.display_name} ({user.id}) -> {cmd_name} ({params_text})")

        if guild:
            embed = discord.Embed(
                title="📜 指令使用紀錄",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(
                name=f"{user.display_name} ({user.name})",
                icon_url=user.display_avatar.url
            )
            embed.add_field(name="👤 執行者", value=f"{user.mention}\n`{user.id}`", inline=True)
            embed.add_field(name="⌨️ 使用指令", value=f"**`{cmd_name}`**", inline=True)
            embed.add_field(
                name="📍 執行頻道",
                value=channel.mention if hasattr(channel, "mention") else "私訊",
                inline=True
            )
            embed.add_field(name="⚙️ 輸入參數", value=params_text[:1000], inline=False)
            embed.set_footer(text=f"伺服器：{guild.name}")

            await self._dispatch_to_log_channel(guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandLoggerCog(bot))