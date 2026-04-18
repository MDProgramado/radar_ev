from telegram import Bot
from telegram.error import TelegramError
import structlog
from radar_ev.config import settings

logger = structlog.get_logger()

class TelegramSender:
    def __init__(self):
        self.bot = Bot(token=settings.telegram_token)

    async def send_opportunities(self, opportunities):
        for opp in opportunities:
            message = (
                f"📊 *Oportunidade +EV*\n"
                f"🏆 {opp.match.home_team} vs {opp.match.away_team}\n"
                f"🎯 Mercado: {opp.market}\n"
                f"💰 Odd oferecida: {opp.offered_odd:.2f}\n"
                f"📈 Odd justa: {opp.fair_odd:.2f}\n"
                f"✅ EV: {opp.ev_percent:.1f}%\n"
                f"🔍 Confiança: {opp.confidence:.0%}"
            )
            try:
                await self.bot.send_message(chat_id=settings.telegram_chat_id, text=message, parse_mode="Markdown")
                logger.info("telegram_sent", market=opp.market)
            except TelegramError as e:
                logger.exception("telegram_failed", error=str(e))
