"""
telegram.py — Envio de alertas de oportunidades +EV via Telegram.

Envia mensagens formatadas em Markdown para um chat/grupo do Telegram
usando a biblioteca python-telegram-bot (assíncrona).

Formato da mensagem:
    📊 *Oportunidade +EV*
    🏆 Palmeiras vs Corinthians
    🎯 Mercado: corners_over_9.5
    💰 Odd oferecida: 1.85
    📈 Odd justa: 2.10
    ✅ EV: 13.5%
    🔍 Confiança: 72%
"""

from typing import List

import structlog
from telegram import Bot
from telegram.error import TelegramError

from radar_ev.config import settings
from radar_ev.models import Opportunity

logger = structlog.get_logger(__name__)


class TelegramSender:
    """Envia oportunidades +EV para o Telegram.

    Cada oportunidade é enviada como uma mensagem individual formatada
    em Markdown, com todos os detalhes relevantes.

    Usage:
        sender = TelegramSender()
        await sender.send_opportunities(opportunities)
    """

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        """Inicializa o bot do Telegram.

        Args:
            token: Token do bot. Se None, usa settings.telegram_token.
            chat_id: ID do chat. Se None, usa settings.telegram_chat_id.
        """
        self.token = token or settings.telegram_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.bot = Bot(token=self.token)

    async def send_opportunities(self, opportunities: List[Opportunity]) -> int:
        """Envia todas as oportunidades como mensagens individuais.

        Args:
            opportunities: Lista de oportunidades +EV aprovadas.

        Returns:
            Número de mensagens enviadas com sucesso.
        """
        if not opportunities:
            logger.info("telegram_no_opportunities", count=0)
            return 0

        sent_count = 0

        # Envia resumo inicial
        header = (
            f"🔔 *Radar +EV — {len(opportunities)} oportunidade(s) encontrada(s)*\n"
            f"{'─' * 30}\n"
        )
        await self._send_message(header)

        # Envia cada oportunidade
        for i, opp in enumerate(opportunities, 1):
            message = self._format_opportunity(opp, index=i, total=len(opportunities))
            success = await self._send_message(message)
            if success:
                sent_count += 1

        # Envia rodapé
        footer = (
            f"\n{'─' * 30}\n"
            f"📋 Total: {sent_count}/{len(opportunities)} enviadas\n"
            f"⚠️ _Não automatiza apostas. Analise e decida manualmente._"
        )
        await self._send_message(footer)

        logger.info(
            "telegram_batch_sent",
            total=len(opportunities),
            sent=sent_count,
        )
        return sent_count

    async def send_status(self, message: str) -> bool:
        """Envia uma mensagem de status (pipeline iniciado, erro, etc.).

        Args:
            message: Texto da mensagem (suporta Markdown).

        Returns:
            True se enviada com sucesso.
        """
        return await self._send_message(f"ℹ️ {message}")

    async def _send_message(self, text: str) -> bool:
        """Envia uma mensagem individual ao Telegram.

        Args:
            text: Texto formatado em Markdown.

        Returns:
            True se enviada com sucesso, False em caso de erro.
        """
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            return True
        except TelegramError as exc:
            logger.error(
                "telegram_send_failed",
                error=str(exc),
                chat_id=self.chat_id,
            )
            return False

    @staticmethod
    def _format_opportunity(opp: Opportunity, index: int = 1, total: int = 1) -> str:
        """Formata uma oportunidade como mensagem Markdown para o Telegram.

        Args:
            opp: Oportunidade +EV.
            index: Número sequencial da oportunidade.
            total: Total de oportunidades no lote.

        Returns:
            String formatada em Markdown.
        """
        # Emoji de nível de EV
        if opp.ev_percent >= 15:
            ev_emoji = "🔥"
            ev_label = "ALTO"
        elif opp.ev_percent >= 10:
            ev_emoji = "🟢"
            ev_label = "BOM"
        elif opp.ev_percent >= 5:
            ev_emoji = "🟡"
            ev_label = "MODERADO"
        else:
            ev_emoji = "🔵"
            ev_label = "BAIXO"

        # Emoji de confiança
        confidence_pct = opp.confidence * 100
        if confidence_pct >= 70:
            conf_emoji = "💪"
        elif confidence_pct >= 50:
            conf_emoji = "👍"
        else:
            conf_emoji = "🤔"

        message = (
            f"📊 *Oportunidade +EV ({index}/{total})* {ev_emoji}\n"
            f"{'─' * 25}\n"
            f"🏆 *{opp.match.home_team}* vs *{opp.match.away_team}*\n"
            f"🏟️ Liga: {opp.match.league}\n"
            f"🎯 Mercado: `{opp.market}`\n"
            f"💰 Odd oferecida: *{opp.offered_odd:.2f}*\n"
            f"📈 Odd justa: *{opp.fair_odd:.2f}*\n"
            f"✅ EV: *{opp.ev_percent:.1f}%* ({ev_label})\n"
            f"{conf_emoji} Confiança: *{confidence_pct:.0f}%*\n"
            f"💵 Sugestão de Banca (Kelly): *{getattr(opp, 'recommended_stake_percent', 0.0):.2f}%*\n"
        )

        # Informações extras (se disponíveis)
        if opp.match.referee:
            message += f"👨‍⚖️ Árbitro: {opp.match.referee}\n"

        if opp.reasoning:
            message += f"📝 _{opp.reasoning}_\n"

        return message
