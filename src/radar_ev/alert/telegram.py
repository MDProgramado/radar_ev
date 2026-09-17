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
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
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
            
            # Botão interativo para apostar (pode colocar link de afiliado aqui)
            keyboard = [[InlineKeyboardButton("💸 Apostar na Betano", url="https://br.betano.com/sport/futebol/")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            success = await self._send_message(message, reply_markup=reply_markup)
            if success:
                sent_count += 1

        # Envia a Múltipla do Dia (Aposta Integrada) se houver 2 ou mais jogos
        if len(opportunities) >= 2:
            # Pega até as 4 melhores oportunidades (maior confiança) para a múltipla
            top_opps = sorted(opportunities, key=lambda o: o.confidence, reverse=True)[:4]
            
            combo_odd = 1.0
            combo_prob = 1.0
            bilhete_text = "🔥 <b>BILHETE MÚLTIPLO DO DIA (APOSTA INTEGRADA)</b> 🔥\n"
            bilhete_text += "Combinando as entradas mais seguras para alavancagem:\n\n"
            
            for o in top_opps:
                combo_odd *= o.offered_odd
                combo_prob *= o.confidence
                bilhete_text += f"▪️ <b>{o.match.home_team} vs {o.match.away_team}</b>\n"
                bilhete_text += f"   ↳ Mercado: <code>{o.market}</code> (Odd: {o.offered_odd:.2f})\n"
                
            combo_ev = (combo_prob * combo_odd) - 1.0
            
            bilhete_text += f"\n{'─' * 25}\n"
            bilhete_text += f"💰 <b>Odd Total da Múltipla:</b> {combo_odd:.2f}\n"
            bilhete_text += f"🎯 <b>Probabilidade de Acerto:</b> {combo_prob*100:.1f}%\n"
            bilhete_text += f"🚀 <b>EV do Bilhete:</b> {combo_ev*100:.1f}%\n"
            bilhete_text += "💡 <i>Dica: Use 0.5% a 1% da banca nessa Múltipla.</i>\n"
            
            keyboard_multipla = [[InlineKeyboardButton("🔥 Montar Múltipla na Betano", url="https://br.betano.com/sport/futebol/")]]
            reply_markup_multipla = InlineKeyboardMarkup(keyboard_multipla)
            
            await self._send_message(bilhete_text, reply_markup=reply_markup_multipla)

        # Envia rodapé
        footer = (
            f"\n{'─' * 30}\n"
            f"📋 Total: {sent_count}/{len(opportunities)} enviadas individuais\n"
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

    async def _send_message(self, text: str, reply_markup=None) -> bool:
        """Envia uma mensagem individual ao Telegram.

        Args:
            text: Texto formatado em Markdown/HTML.
            reply_markup: Botões interativos (opcional).

        Returns:
            True se enviada com sucesso, False em caso de erro.
        """
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return True
        except TelegramError as exc:
            logger.error(
                "telegram_send_failed",
                error=str(exc),
                chat_id=self.chat_id,
            )
            # Fallback: salva a mensagem em um arquivo de texto local
            try:
                import datetime
                import re
                
                # Remove tags HTML básicas para o texto puro
                clean_text = re.sub(r'<[^>]+>', '', text)
                
                with open("telegram_fallback.txt", "a", encoding="utf-8") as f:
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"\n--- ALERTA NÃO ENVIADO ({timestamp}) ---\n")
                    f.write(clean_text)
                    f.write(f"\nErro: {str(exc)}\n")
                    f.write("-" * 40 + "\n")
            except Exception as fallback_exc:
                logger.error("telegram_fallback_failed", error=str(fallback_exc))
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

        is_diamond = confidence_pct >= 90.0 and opp.ev_percent >= 10.0
        if is_diamond:
            message = (
                f"💎 <b>ENTRADA DIAMANTE (MAX BET)</b> 💎\n"
                f"{'━' * 25}\n"
            )
        else:
            message = (
                f"📊 <b>Oportunidade +EV ({index}/{total})</b> {ev_emoji}\n"
                f"{'─' * 25}\n"
            )
            
        message += (
            f"🏆 <b>{opp.match.home_team}</b> vs <b>{opp.match.away_team}</b>\n"
            f"🏟️ Liga: {opp.match.league}\n"
            f"🎯 Mercado: <code>{opp.market}</code>\n"
            f"💰 Odd oferecida: <b>{opp.offered_odd:.2f}</b>\n"
            f"📈 Odd justa: <b>{opp.fair_odd:.2f}</b>\n"
            f"✅ EV: <b>{opp.ev_percent:.1f}%</b> ({ev_label})\n"
            f"{conf_emoji} Confiança: <b>{confidence_pct:.0f}%</b>\n"
            f"💵 Sugestão de Banca (Kelly): <b>{getattr(opp, 'recommended_stake_percent', 0.0):.2f}%</b>\n"
        )

        # Informações extras (se disponíveis)
        if opp.match.referee:
            message += f"👨‍⚖️ Árbitro: {opp.match.referee}\n"

        if opp.reasoning:
            message += f"📝 <i>{opp.reasoning}</i>\n"

        return message
