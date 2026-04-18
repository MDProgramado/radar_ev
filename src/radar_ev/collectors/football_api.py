from datetime import datetime
from typing import List
from radar_ev.config import settings
from radar_ev.http_client import HTTPClient
from radar_ev.models import Match
from radar_ev.models import Odds
import math
from scipy.stats import poisson 
import structlog
logger = structlog.get_logger()


class FootballAPICollector:
    BASE_URL = "https://v3.football.api-sports.io"

    def __init__(self):
        headers = {
            "x-apisports-key": settings.rapidapi_key,
        }
        self.client = HTTPClient(base_url=self.BASE_URL, headers=headers, timeout=30.0)

    async def get_today_matches(self, date: datetime) -> List[Match]:
        endpoint = "/fixtures"
        params = {
            "date": date.strftime("%Y-%m-%d"),
            "timezone": "America/Sao_Paulo"
        }
        data = await self.client.get(endpoint, params=params)

        matches = []
        for fixture in data.get("response", []):
            match = Match(
                id=fixture["fixture"]["id"],
                home_team=fixture["teams"]["home"]["name"],
                away_team=fixture["teams"]["away"]["name"],
                datetime=datetime.fromisoformat(fixture["fixture"]["date"]),
                league=fixture["league"]["name"],
                referee=fixture["fixture"].get("referee"),
                home_lineup_confirmed=False,
                away_lineup_confirmed=False,
                motivation_index=0.0,
            )
            matches.append(match)
        return matches

    async def get_team_stats(self, team_id: int, league_id: int, season: int) -> dict:
        endpoint = "/teams/statistics"
        params = {"team": team_id, "league": league_id, "season": season}
        return await self.client.get(endpoint, params=params)

    async def get_lineups(self, fixture_id: int) -> dict:
        endpoint = "/fixtures/lineups"
        params = {"fixture": fixture_id}
        return await self.client.get(endpoint, params=params)

    async def get_referee_stats(self, referee_name: str) -> dict:
        endpoint = "/fixtures"
        params = {"referee": referee_name}
        return await self.client.get(endpoint, params=params)

    async def get_odds(self, fixture_id: int) -> List[Odds]:
        """
        Obtém as odds da Betano para uma partida específica usando o endpoint /odds.
        """
        endpoint = "/odds"
        params = {"fixture": fixture_id}
        
        try:
            data = await self.client.get(endpoint, params=params)
            odds_list = []
            
            for fixture_data in data.get("response", []):
                for bookmaker in fixture_data.get("bookmakers", []):
                    if bookmaker.get("name") == "Betano":
                        for bet in bookmaker.get("bets", []):
                            market_name = f"{bet.get('name')}_{bet.get('value')}"
                            for odd_detail in bet.get("odds", []):
                                odd_value = float(odd_detail.get("odd", 0))
                                if odd_value > 0:
                                    odds_list.append(
                                        Odds(
                                            match_id=fixture_id,
                                            market=market_name,
                                            odd_value=odd_value,
                                            offered_by="betano"
                                        )
                                    )
            return odds_list
        except Exception:
            # O http_client já loga o erro, apenas retornamos lista vazia
            return []
        
    
    async def get_team_statistics(self, team_id: int, league_id: int, season: int) -> dict:
        """Retorna estatísticas agregadas de um time em uma liga/temporada."""
        endpoint = "/teams/statistics"
        params = {
            "team": team_id,
            "league": league_id,
            "season": season
        }
        return await self.client.get(endpoint, params=params)

    def poisson_probability(lambda_avg, k):
        """Retorna P(X = k) para uma distribuição Poisson com média lambda_avg."""
        return poisson.pmf(k, lambda_avg)

    def prob_over_9_5_corners(home_avg_corners, away_avg_corners_conceded):
        # Estimativa simples: média de escanteios do time + média de escanteios sofridos pelo adversário
        lambda_total = home_avg_corners + away_avg_corners_conceded
        # Probabilidade de total > 9.5 = 1 - P(X <= 9)
        prob = 1 - sum(poisson.pmf(k, lambda_total) for k in range(10))
        return prob
    async def close(self):
        await self.client.close()
