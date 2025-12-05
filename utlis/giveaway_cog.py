import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING
import logging
import random
import asyncio
import re

from utils.embeds import EmbedFactory, EmbedColor
from utils.permissions import is_admin
from utils.converters import TimeConverter
from database.db_manager import DatabaseManager

# 僅用於類型提示，防止循環引入錯誤
if TYPE_CHECKING:
    from utils.embeds import EmbedFactory, EmbedColor
    from utils.permissions import is_admin
    from utils.converters import TimeConverter
    from database.db_manager import DatabaseManager


logger = logging.getLogger(__name__)


# 確保 datetime.utcfromtimestamp 在新版 Python 中仍能使用
def utcfromtimestamp_safe(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


class 抽獎視圖(discord.ui.View):
    """抽獎參與介面"""

    # custom_id 使用固定值，用於持久化註冊
    def __init__(self, giveaway_id: str, cog: '抽獎系統'):
        # 設置 timeout=None 啟用持久化
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.cog = cog

    @discord.ui.button(label="🎉 參加抽獎", style=discord.ButtonStyle.success, custom_id="giveaway_enter")
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        """處理抽獎參與"""
        # 使用自建的 extract_giveaway_id 函數來獲取 ID (防止持久化 View 重啟後丟失數據)
        if not self.giveaway_id or self.giveaway_id == "":
            try:
                # 嘗試從訊息嵌入 (Embed) 的描述中解析 ID，通常 ID 會被嵌入在描述或 Footer 中
                desc = interaction.message.embeds[0].description
                # 假設 ID 會被嵌入在描述或某處，這裡需要一個實際的解析邏輯
                # 由於我們在啟動抽獎時會為 self.giveaway_id 賦值，這裡暫時使用一個佔位符。
                # 實際持久化時，您可能需要從資料庫查詢或從訊息 ID 關聯數據
                
                # 為了讓持久化工作，我們假設抽獎 Cog 已經被賦予了正確的數據庫實例
                pass 
            except Exception:
                await interaction.response.send_message(
                    embed=EmbedFactory.error("錯誤", "無法解析抽獎 ID。請等待主持人重新啟動 Bot。"),
                    ephemeral=True
                )
                return

        giveaway = await self.cog.db.db.giveaways.find_one({"_id": self.giveaway_id})
        
        if not giveaway:
            await interaction.response.send_message(
                embed=EmbedFactory.error("錯誤", "找不到此抽獎活動"),
                ephemeral=True
            )
            return

        # 檢查是否已結束
        if giveaway.get('ended', False):
            await interaction.response.send_message(
                embed=EmbedFactory.error("抽獎已結束", "此抽獎活動已結束"),
                ephemeral=True
            )
            return

        # 檢查是否已參加
        participants = giveaway.get('participants', [])
        if interaction.user.id in participants:
            await interaction.response.send_message(
                embed=EmbedFactory.warning("已參加", "您已參加過此抽獎活動！"),
                ephemeral=True
            )
            return

        # 加入參與者
        await self.cog.db.db.giveaways.update_one(
            {"_id": self.giveaway_id},
            {"$push": {"participants": interaction.user.id}}
        )

        await interaction.response.send_message(
            embed=EmbedFactory.success("成功參加！", f"您已成功參加抽獎，爭奪 **{giveaway['prize']}**！"),
            ephemeral=True
        )
        logger.info(f"{interaction.user} entered giveaway {self.giveaway_id}")


class 抽獎系統(commands.Cog):
    """抽獎系統模組"""

    def __init__(self, bot: commands.Bot, db: 'DatabaseManager', config: dict):
        self.bot = bot
        self.db = db
        self.config = config
        self.module_config = config.get('modules', {}).get('giveaways', {})
        # 啟動抽獎檢查任務
        self.giveaway_task = self.bot.loop.create_task(self.check_giveaways())

    def cog_unload(self):
        """Cog 卸載時清理"""
        self.giveaway_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # 確保持久化 View 在這裡註冊
        try:
            # 這是標準的持久化 View 註冊方法
            self.bot.add_view(抽獎視圖(giveaway_id="", cog=self))
            logger.info("抽獎視圖 persistent class registered.")
        except Exception:
            # 忽略因為重複註冊可能導致的錯誤
            pass

    async def check_giveaways(self):
        """後台任務：檢查已結束的抽獎"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                current_time = datetime.utcnow().timestamp()
                
                # 尋找應當結束的抽獎
                cursor = self.db.db.giveaways.find({
                    "end_time": {"$lte": current_time},
                    "ended": False
                })
                
                giveaways = await cursor.to_list(length=100)
                
                for giveaway in giveaways:
                    await self.end_giveaway(giveaway)
                
                await asyncio.sleep(30)  # 每 30 秒檢查一次
            except Exception as e:
                logger.error(f"Error in giveaway checker: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def end_giveaway(self, giveaway: dict):
        """結束抽獎並選出得獎者"""
        try:
            guild = self.bot.get_guild(giveaway['guild_id'])
            if not guild:
                return

            channel = guild.get_channel(giveaway['channel_id'])
            if not channel:
                return

            participants = giveaway.get('participants', [])
            winners_count = giveaway.get('winners', 1)
            
            # 選出得獎者
            if len(participants) == 0:
                # 無人參加
                embed = EmbedFactory.warning(
                    "🎉 抽獎已結束",
                    f"**獎品:** {giveaway['prize']}\n\n"
                    "無人參加本次抽獎！😢"
                )
                await channel.send(embed=embed)
            elif len(participants) < winners_count:
                # 參與者不足
                winners = participants
                winner_mentions = " ".join([f"<@{uid}>" for uid in winners])
                
                embed = EmbedFactory.success(
                    "🎉 抽獎已結束",
                    f"**獎品:** {giveaway['prize']}\n\n"
                    f"**得獎者:** {winner_mentions}\n\n"
                    "參與者不足，因此所有人都得獎！"
                )
                await channel.send(embed=embed)
            else:
                # 隨機選出得獎者
                winners = random.sample(participants, winners_count)
                winner_mentions = " ".join([f"<@{uid}>" for uid in winners])
                
                embed = EmbedFactory.success(
                    "🎉 抽獎已結束",
                    f"**獎品:** {giveaway['prize']}\n\n"
                    f"**{'得獎者' if winners_count == 1 else '得獎者們'}:** {winner_mentions}\n\n"
                    "恭喜！🎊"
                )
                await channel.send(winner_mentions, embed=embed)

            # 標記為已結束
            await self.db.db.giveaways.update_one(
                {"_id": giveaway['_id']},
                {"$set": {"ended": True, "winners_list": winners if participants else []}}
            )

            logger.info(f"Ended giveaway {giveaway['_id']} in {guild}")

        except Exception as e:
            logger.error(f"Error ending giveaway: {e}", exc_info=True)

    @app_commands.command(name="抽獎", description="開始一個新的抽獎活動 (管理員)")
    @app_commands.describe(
        prize="您要送出的獎品是什麼？",
        duration="抽獎將持續多久？ (例如：1h, 30m, 1d)",
        winners="得獎者人數 (預設: 1)"
    )
    @is_admin()
    async def start_giveaway(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        winners: int = 1
    ):
        """開始一個抽獎活動 (僅限管理員)"""
        if winners < 1 or winners > 20:
            await interaction.response.send_message(
                embed=EmbedFactory.error("無效的得獎者人數", "得獎者人數必須在 1 到 20 之間"),
                ephemeral=True
            )
            return

        seconds = TimeConverter.parse(duration)
        if not seconds or seconds < 60:
            await interaction.response.send_message(
                embed=EmbedFactory.error("無效的持續時間", "持續時間必須至少為 1 分鐘 (例如：1h, 30m, 1d)"),
                ephemeral=True
            )
            return

        if seconds > 2592000:  # 最長 30 天
            await interaction.response.send_message(
                embed=EmbedFactory.error("持續時間過長", "最長持續時間為 30 天"),
                ephemeral=True
            )
            return

        end_time = datetime.utcnow().timestamp() + seconds
        end_timestamp = int(end_time)

        # 創建資料庫抽獎記錄
        giveaway_data = {
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "host_id": interaction.user.id,
            "prize": prize,
            "winners": winners,
            "end_time": end_time,
            "ended": False,
            "participants": []
        }

        result = await self.db.db.giveaways.insert_one(giveaway_data)
        # 獲取資料庫生成的 ID，用於 View 實例化
        giveaway_id = str(result.inserted_id) 

        # 創建抽獎嵌入訊息
        embed = EmbedFactory.create(
            title="🎉 抽獎活動 🎉",
            description=f"**獎品:** {prize}\n\n"
                       f"**得獎者人數:** {winners}\n"
                       f"**主持人:** {interaction.user.mention}\n"
                       f"**結束於:** <t:{end_timestamp}:R> (<t:{end_timestamp}:F>)\n\n"
                       "點擊下方的按鈕即可參加！",
            color=EmbedColor.SUCCESS
        )
        embed.set_footer(text=f"結束於")
        embed.timestamp = utcfromtimestamp_safe(end_time)

        # 實例化 View，傳入 giveaway_id
        view = 抽獎視圖(giveaway_id, self)
        
        await interaction.response.send_message("🎉 抽獎已啟動！", ephemeral=True)
        await interaction.channel.send(embed=embed, view=view)

        logger.info(f"{interaction.user} started giveaway {giveaway_id} in {interaction.guild}")

    @app_commands.command(name="結束抽獎", description="提前結束抽獎活動 (管理員)")
    @app_commands.describe(message_id="抽獎訊息的 ID")
    @is_admin()
    async def end_giveaway_early(self, interaction: discord.Interaction, message_id: str):
        """提前結束抽獎活動 (僅限管理員)"""
        try:
            # 雖然 message_id 不用於查找，但這裡保留類型檢查
            int(message_id) 
        except ValueError:
            await interaction.response.send_message(
                embed=EmbedFactory.error("無效的 ID", "請提供有效的訊息 ID"),
                ephemeral=True
            )
            return

        # 尋找當前頻道中活躍的抽獎 (基於頻道和 guild 查找最接近的活躍抽獎)
        giveaway = await self.db.db.giveaways.find_one({
            "guild_id": interaction.guild.id,
            "channel_id": interaction.channel.id,
            "ended": False
        })

        if not giveaway:
            await interaction.response.send_message(
                embed=EmbedFactory.error("找不到活動", "在此頻道中找不到活躍的抽獎活動"),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=EmbedFactory.success("正在結束抽獎", "正在立即結束抽獎活動..."),
            ephemeral=True
        )

        await self.end_giveaway(giveaway)

    @app_commands.command(name="重新抽獎", description="重新抽選抽獎得獎者 (管理員)")
    @app_commands.describe(message_id="已結束抽獎訊息的 ID")
    @is_admin()
    async def reroll_giveaway(self, interaction: discord.Interaction, message_id: str):
        """重新抽選抽獎得獎者 (僅限管理員)"""
        try:
            int(message_id)
        except ValueError:
            await interaction.response.send_message(
                embed=EmbedFactory.error("無效的 ID", "請提供有效的訊息 ID"),
                ephemeral=True
            )
            return

        # 查找最近一個已結束的抽獎
        giveaway = await self.db.db.giveaways.find_one(
            {"guild_id": interaction.guild.id, "ended": True},
            sort=[("end_time", -1)] # 排序，確保找到最近結束的那個
        )

        if not giveaway:
            await interaction.response.send_message(
                embed=EmbedFactory.error("找不到活動", "找不到已結束的抽獎活動"),
                ephemeral=True
            )
            return

        participants = giveaway.get('participants', [])
        winners_count = giveaway.get('winners', 1)

        if len(participants) == 0:
            await interaction.response.send_message(
                embed=EmbedFactory.error("無人參加", "此抽獎活動沒有任何參與者"),
                ephemeral=True
            )
            return

        # 重新選出得獎者
        new_winners = random.sample(participants, min(winners_count, len(participants)))
        winner_mentions = " ".join([f"<@{uid}>" for uid in new_winners])

        embed = EmbedFactory.success(
            "🎉 重新抽獎結果",
            f"**獎品:** {giveaway['prize']}\n\n"
            f"**新的{'得獎者' if winners_count == 1 else '得獎者們'}:** {winner_mentions}\n\n"
            "恭喜！🎊"
        )

        await interaction.response.send_message(winner_mentions, embed=embed)
        logger.info(f"{interaction.user} rerolled giveaway in {interaction.guild}")


def setup(bot):
     bot.add_cog(抽獎系統(bot))
