import asyncio
from datetime import datetime, timezone
from radar_ev.config import settings
from radar_ev.collectors.football_api import FootballAPICollector
from radar_ev.collectors.odds_collector import OddsCollector
from radar_ev.rules import apply_all_rules
from radar_ev.ev_calculator import calculate_ev, create_opportunity
from radar_ev.alert.telegram import TelegramSender
from radar_ev.mock_data import get_mock_prediction  # temporário
import structlog
logger = structlog.get_logger()

# Mapeamento de liga para sport_key da The Odds API
LEAGUE_TO_SPORT_KEY = {
    "Brasileirão": "soccer_brazil_campeonato",
    "Premier League": "soccer_epl",
    "La Liga": "soccer_spain_la_liga",
    "Serie A": "soccer_italy_serie_a",
    "Bundesliga": "soccer_germany_bundesliga",
    "Ligue 1": "soccer_france_ligue_one",
    # Adicione mais conforme necessário
}

def get_sport_key(league_name: str) -> str:
    """Retorna o sport_key da The Odds API para uma determinada liga."""
    return LEAGUE_TO_SPORT_KEY.get(league_name, "soccer_brazil_campeonato")  # fallback

async def run_pipeline(use_mock: bool = False):
    print("Iniciando pipeline...")
    opportunities = []

    if use_mock:
        print("Usando dados mockados (sem API real).")
        from radar_ev.mock_data import get_mock_matches, get_mock_odds
        matches = get_mock_matches()
        for match in matches:
            odds_list = get_mock_odds(match.id)
            for odds in odds_list:
                pred = get_mock_prediction(match.id, odds.market)
                ev = calculate_ev(pred, odds.odd_value)
                if ev >= settings.min_ev_percent:
                    opp = create_opportunity(match, pred, odds, ev)
                    ok, reason = apply_all_rules(opp, settings.derby_teams, min_motivation=7.0)
                    if ok:
                        opportunities.append(opp)
                        print(f"Oportunidade mock: {match.home_team} vs {match.away_team} - {opp.market} EV={ev:.2f}%")
                    else:
                        print(f"Filtrado mock: {reason}")
    else:
        print("Usando APIs reais (Football API + Odds via Football API).")
        football = FootballAPICollector()
        try:
            today = datetime.now(timezone.utc)
            matches = await football.get_today_matches(today)
            print(f"Encontrados {len(matches)} jogos reais.")

            # Lista de ligas que a Betano normalmente cobre
            ligas_interesse = [
                "Brasileirão",
                "Premier League",
                "La Liga",
                "Serie A",
                "Bundesliga",
                "Ligue 1",
                "Primeira Liga",
                "Eredivisie",
                "Championship",
                "MLS",
                "Süper Lig",
                "Liga MX",
                "Primeira Liga",
                "Campeonato Paulista",  # se aplicável
                "Copa do Brasil"
            ]

            for match in matches:
                seconds_until_match = (match.datetime - datetime.now(timezone.utc)).total_seconds()
                if seconds_until_match > settings.pre_match_hours * 3600:
                    continue
                if seconds_until_match < 0:
                    continue

                # Filtra apenas ligas de interesse para economizar requisições
                if match.league not in ligas_interesse:
                    continue

                # Pausa para não estourar o rate limit (6 segundos entre chamadas = 10 por minuto)
                await asyncio.sleep(0.6)

                odds_list = await football.get_odds(match.id)
                if not odds_list:
                    print(f"Sem odds da Betano para {match.home_team} vs {match.away_team} ({match.league})")
                    continue

                for odds in odds_list:
                    # Predição mockada (será substituída pelo modelo real depois)
                    pred = get_mock_prediction(match.id, odds.market)
                    ev = calculate_ev(pred, odds.odd_value)
                    if ev >= settings.min_ev_percent:
                        opp = create_opportunity(match, pred, odds, ev)
                        ok, reason = apply_all_rules(opp, settings.derby_teams, min_motivation=7.0)
                        if ok:
                            opportunities.append(opp)
                            print(f"✅ Oportunidade: {match.home_team} vs {match.away_team} - {opp.market} EV={ev:.2f}%")
                        else:
                            print(f"❌ Filtrado: {reason}")
                    else:
                        print(f"⚠️ EV baixo ({ev:.2f}%) para {match.home_team} vs {match.away_team} - {odds.market}")

        except Exception as e:
            logger.exception("API real falhou", error=str(e))
            print("Erro nas APIs reais. Verifique suas chaves e conexão.")
            return
        finally:
            await football.close()  

    if opportunities:
        sender = TelegramSender()
        await sender.send_opportunities(opportunities)
    else:
        print("Nenhuma oportunidade encontrada.")
    print("Pipeline finalizado.")

if __name__ == "__main__":
    # Altere para False se quiser usar APIs reais (desde que tenha chaves válidas)
    asyncio.run(run_pipeline(use_mock=False))