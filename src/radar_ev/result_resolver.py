"""
result_resolver.py — Resolutor Automático de Resultados e Relatório Diário.

Busca no banco de dados as oportunidades pendentes (cujos jogos já terminaram),
consulta a API-Football para obter o resultado real (Placar, Escanteios, Cartões),
valida se a aposta foi GREEN ou RED e calcula o lucro. Em seguida, gera um
relatório de transparência no Telegram.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import structlog

from radar_ev.database import db
from radar_ev.db import get_db_connection
from radar_ev.collectors.football_api import FootballAPICollector
from radar_ev.alert.telegram import TelegramSender

logger = structlog.get_logger(__name__)


class ResultResolver:
    """Verifica resultados de partidas passadas e atualiza o banco de dados."""

    def __init__(self):
        self.db_path = db.db_path
        self.telegram = TelegramSender()

    def get_pending_opportunities(self):
        """Busca apostas pendentes de jogos que já deveriam ter terminado."""
        now = datetime.now(timezone.utc)
        # Um jogo de futebol dura em média 2 horas
        cutoff_time = (now - timedelta(hours=2.5)).isoformat()
        
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM opportunities WHERE status = 'PENDING' AND match_date < ?",
                (cutoff_time,)
            )
            return cursor.fetchall()

    async def resolve_all(self):
        """Resolve todas as apostas pendentes."""
        pending = self.get_pending_opportunities()
        if not pending:
            logger.info("no_pending_results")
            return

        print(f"🔍 Encontradas {len(pending)} oportunidades pendentes para resolver.")
        
        greens = 0
        reds = 0
        profit_total = 0.0
        resolved_count = 0

        async with FootballAPICollector() as football:
            for row in pending:
                match_id = row['match_id']
                market = row['market']
                offered_odd = row['offered_odd']
                opp_id = row['id']
                stake = row['recommended_stake'] or 1.0  # Assumindo 1% se for 0 para fins de relatório

                # Puxa o resultado final do jogo
                try:
                    fixture_data = await self._fetch_fixture_results(football, match_id)
                    if not fixture_data:
                        continue
                        
                    # Verifica se o jogo realmente terminou
                    status_short = fixture_data.get("fixture", {}).get("status", {}).get("short")
                    if status_short not in ["FT", "AET", "PEN"]:
                        continue  # Jogo ainda não terminou ou foi cancelado

                    # Persiste o placar real (independente do mercado) — dado
                    # para treinar o modelo Dixon-Coles (INSERT OR REPLACE).
                    self._save_match_result(fixture_data)

                    # Avalia a aposta
                    won = self._evaluate_bet(market, fixture_data)
                    if won is None:
                        continue  # Mercado não suportado pelo resolutor ainda

                    # Calcula lucro/prejuízo
                    profit = (stake * offered_odd - stake) if won else -stake

                    # Atualiza o banco
                    self._update_opportunity(opp_id, won, profit)
                    
                    if won:
                        greens += 1
                    else:
                        reds += 1
                        
                    profit_total += profit
                    resolved_count += 1
                    
                    # Rate limit safety
                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error("resolve_failed", opp_id=opp_id, error=str(e))

        # Envia Relatório se houver resolvidos
        if resolved_count > 0:
            await self._send_daily_report(greens, reds, profit_total)

    async def _fetch_fixture_results(self, football: FootballAPICollector, match_id: int) -> dict:
        """Busca estatísticas de um jogo específico pelo ID."""
        endpoint = "/fixtures"
        params = {"id": match_id}
        try:
            data = await football.client.get(endpoint, params=params)
            response = data.get("response", [])
            return response[0] if response else {}
        except Exception as e:
            logger.error("fetch_fixture_failed", match_id=match_id, error=str(e))
            return {}

    def _evaluate_bet(self, market: str, fixture_data: dict) -> bool:
        """Avalia se a aposta ganhou ou perdeu baseada nas estatísticas do jogo."""
        try:
            # Placar final
            goals_home = fixture_data.get("goals", {}).get("home", 0) or 0
            goals_away = fixture_data.get("goals", {}).get("away", 0) or 0
            total_goals = goals_home + goals_away

            # Estatísticas do jogo (escanteios, cartões)
            stats = fixture_data.get("statistics", [])
            total_corners = 0
            total_cards = 0
            
            for team_stat in stats:
                for stat in team_stat.get("statistics", []):
                    name = stat.get("type", "")
                    value = stat.get("value") or 0
                    
                    if name == "Corner Kicks":
                        total_corners += int(value)
                    elif name == "Yellow Cards" or name == "Red Cards":
                        total_cards += int(value)

            market_lower = market.lower()
            
            # Avalia Gols
            if "goals_over_" in market_lower:
                threshold = float(market_lower.split("goals_over_")[1])
                return total_goals > threshold
            elif "goals_under_" in market_lower:
                threshold = float(market_lower.split("goals_under_")[1])
                return total_goals < threshold
                
            # Avalia Escanteios
            elif "corners_over_" in market_lower:
                threshold = float(market_lower.split("corners_over_")[1])
                return total_corners > threshold
            elif "corners_under_" in market_lower:
                threshold = float(market_lower.split("corners_under_")[1])
                return total_corners < threshold
                
            # Avalia Cartões
            elif "cards_over_" in market_lower:
                threshold = float(market_lower.split("cards_over_")[1])
                return total_cards > threshold
            elif "cards_under_" in market_lower:
                threshold = float(market_lower.split("cards_under_")[1])
                return total_cards < threshold

            return None # Mercado desconhecido

        except Exception as e:
            logger.error("evaluate_bet_failed", market=market, error=str(e))
            return None

    def _save_match_result(self, fixture_data: dict) -> None:
        """Insere/atualiza o placar real em ``match_results`` (idempotente).

        Extrai de /fixtures?id=X: ``fixture.goals.home`` e
        ``fixture.goals.away``, além de times/liga/temporada. Usa INSERT OR
        REPLACE para que re-resoluções não dupliquem linhas (match_id é a chave).
        """
        status_short = fixture_data.get("fixture", {}).get("status", {}).get("short")
        if status_short not in ["FT", "AET", "PEN"]:
            return

        goals = fixture_data.get("goals", {}) or {}
        teams = fixture_data.get("teams", {}) or {}
        league = fixture_data.get("league", {}) or {}
        match_id = fixture_data.get("fixture", {}).get("id")
        if match_id is None:
            return

        try:
            with get_db_connection(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO match_results (
                        match_id, home_team, away_team, home_score, away_score,
                        league, season, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    match_id,
                    (teams.get("home") or {}).get("name"),
                    (teams.get("away") or {}).get("name"),
                    goals.get("home"),
                    goals.get("away"),
                    league.get("name"),
                    league.get("season"),
                    datetime.now(timezone.utc).isoformat(),
                ))
                conn.commit()
            logger.info("match_result_saved", match_id=match_id)
        except Exception as exc:
            logger.error("match_result_save_failed", match_id=match_id, error=str(exc))

    def _update_opportunity(self, opp_id: int, won: bool, profit: float):
        """Atualiza a linha no banco de dados para RESOLVED."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE opportunities 
                SET status = 'RESOLVED', result_won = ?, profit = ?
                WHERE id = ?
            """, (won, profit, opp_id))
            conn.commit()

    async def _send_daily_report(self, greens: int, reds: int, profit: float):
        """Envia o extrato diário de lucratividade para o Telegram."""
        total = greens + reds
        win_rate = (greens / total * 100) if total > 0 else 0
        
        emoji_lucro = "🤑" if profit > 0 else "📉"
        
        # Banca virtual simulada para efeitos de marketing
        banca_inicial = 1000.00
        lucro_rs = banca_inicial * (profit / 100.0)
        banca_final = banca_inicial + lucro_rs
        
        msg = (
            f"📊 <b>EXTRATO DE RESULTADOS (FECHAMENTO)</b>\n"
            f"{'─' * 30}\n"
            f"✅ <b>Greens:</b> {greens}\n"
            f"❌ <b>Reds:</b> {reds}\n"
            f"🎯 <b>Taxa de Acerto:</b> {win_rate:.1f}%\n"
            f"{'─' * 30}\n"
            f"{emoji_lucro} <b>Balanço do Dia:</b> {profit:+.2f}%\n"
            f"🏦 <b>Simulação de Banca:</b>\n"
            f"   ↳ Inicial: R$ 1.000,00\n"
            f"   ↳ Lucro Diário: R$ {lucro_rs:+.2f}\n"
            f"   ↳ <b>Saldo Final: R$ {banca_final:.2f}</b>\n\n"
            f"<i>*Balanço calculado com base no Critério de Kelly.</i>"
        )
        
        await self.telegram.send_status(msg)
        print("✅ Relatório diário enviado para o Telegram.")


def main():
    import sys
    resolver = ResultResolver()
    run_daemon = "--daemon" in sys.argv or "-d" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print("Radar +EV — Juiz de Resultados")
        print("Uso: python -m radar_ev.result_resolver [opções]")
        print("  --daemon, -d  Executar continuamente a cada 6 horas")
        sys.exit(0)

    async def _daemon_loop():
        print("🚀 Iniciando Juiz de Resultados em modo DAEMON...")
        while True:
            try:
                await resolver.resolve_all()
                print(f"⏳ Aguardando 6 horas para a próxima varredura de resultados...")
                await asyncio.sleep(21600)  # 6 horas
            except KeyboardInterrupt:
                print("🛑 Daemon interrompido.")
                break
            except Exception as e:
                print(f"💥 Erro no daemon do juiz: {e}")
                await asyncio.sleep(300)

    if run_daemon:
        asyncio.run(_daemon_loop())
    else:
        asyncio.run(resolver.resolve_all())


if __name__ == "__main__":
    main()
