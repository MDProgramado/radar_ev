"""
football_api.py — Wrapper da API-Football (v3.football.api-sports.io).

Encapsula todas as chamadas à API-Football, parseando respostas em objetos
de domínio (Match, Odds). Utiliza o HTTPClient com retry e logging.

Endpoints utilizados:
- /fixtures — Lista de jogos (por data, liga, etc.)
- /odds — Odds de todos os bookmakers para uma partida.
- /teams/statistics — Estatísticas agregadas de um time (escanteios, cartões, etc.)
- /fixtures/lineups — Escalações confirmadas (placeholder).
"""

from datetime import datetime
from typing import List, Optional

import structlog

from radar_ev.config import settings
from radar_ev.http_client import HTTPClient, RateLimitError
from radar_ev.models import Match, Odds

logger = structlog.get_logger(__name__)


class FootballAPICollector:
    """Coletor de dados da API-Football.

    Responsável por:
    - Buscar partidas do dia com IDs de time/liga/temporada.
    - Buscar odds filtradas por bookmaker (Betano).
    - Buscar estatísticas históricas de times.
    - Buscar escalações (futuro).

    Usage:
        collector = FootballAPICollector()
        matches = await collector.get_today_matches(datetime.now())
        odds = await collector.get_odds(match.id)
        await collector.close()
    """

    BASE_URL = "https://v3.football.api-sports.io"

    def __init__(self) -> None:
        headers = {
            "x-apisports-key": settings.rapidapi_key,
        }
        self.client = HTTPClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30.0,
        )

    async def get_today_matches(self, date: datetime) -> List[Match]:
        """Busca todas as partidas de uma data específica.

        Args:
            date: Data para busca (usa apenas a parte da data, ignora hora).

        Returns:
            Lista de objetos Match com metadados completos.
        """
        endpoint = "/fixtures"
        params = {
            "date": date.strftime("%Y-%m-%d"),
            "timezone": "America/Sao_Paulo",
        }

        data = await self.client.get(endpoint, params=params)
        matches: List[Match] = []

        for fixture in data.get("response", []):
            try:
                fixture_info = fixture.get("fixture", {})
                teams_info = fixture.get("teams", {})
                league_info = fixture.get("league", {})

                match = Match(
                    id=fixture_info["id"],
                    home_team=teams_info["home"]["name"],
                    away_team=teams_info["away"]["name"],
                    datetime=datetime.fromisoformat(fixture_info["date"]),
                    league=league_info["name"],
                    referee=fixture_info.get("referee"),
                    home_lineup_confirmed=False,
                    away_lineup_confirmed=False,
                    motivation_index=0.0,
                    # IDs para consulta de estatísticas no predictor
                    home_team_id=teams_info["home"].get("id"),
                    away_team_id=teams_info["away"].get("id"),
                    league_id=league_info.get("id"),
                    season=league_info.get("season"),
                )
                matches.append(match)

            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "match_parse_error",
                    fixture_id=fixture.get("fixture", {}).get("id"),
                    error=str(exc),
                )
                continue

        logger.info("matches_fetched", count=len(matches), date=date.strftime("%Y-%m-%d"))
        return matches

    async def get_odds(self, fixture_id: int) -> List[Odds]:
        """Busca odds da Betano para uma partida via /odds.

        Filtra apenas odds do bookmaker "Betano". Se a Betano não estiver
        disponível, retorna lista vazia (esperado para muitas ligas).

        Args:
            fixture_id: ID da partida na API-Football.

        Returns:
            Lista de Odds da Betano para todos os mercados disponíveis.
        """
        endpoint = "/odds"
        params = {"fixture": fixture_id}

        try:
            data = await self.client.get(endpoint, params=params)
            odds_list: List[Odds] = []

            for fixture_data in data.get("response", []):
                for bookmaker in fixture_data.get("bookmakers", []):
                    # Filtra apenas Betano
                    if bookmaker.get("name", "").lower() != "betano":
                        continue

                    for bet in bookmaker.get("bets", []):
                        bet_name = bet.get("name", "unknown")
                        bet_values = bet.get("values", bet.get("odds", []))

                        for odd_detail in bet_values:
                            try:
                                # A estrutura pode variar: odd_detail pode ter "odd" ou "value"
                                odd_value_raw = odd_detail.get("odd") or odd_detail.get("value", 0)
                                odd_value = float(odd_value_raw)
                                odd_label = odd_detail.get("value", odd_detail.get("name", ""))

                                if odd_value > 0:
                                    # Nome do mercado: "tipo_valor" (ex: "Over/Under_Over 9.5")
                                    market_name = self._normalize_market_name(bet_name, str(odd_label))

                                    odds_list.append(
                                        Odds(
                                            match_id=fixture_id,
                                            market=market_name,
                                            odd_value=odd_value,
                                            offered_by="betano",
                                        )
                                    )
                            except (ValueError, TypeError):
                                continue

            logger.info(
                "odds_fetched",
                fixture_id=fixture_id,
                count=len(odds_list),
                bookmaker="betano",
            )
            return odds_list

        except RateLimitError:
            logger.warning("odds_rate_limited", fixture_id=fixture_id)
            raise  # Propaga para o orquestrador controlar

        except Exception as exc:
            logger.error(
                "odds_fetch_failed",
                fixture_id=fixture_id,
                error=str(exc),
            )
            return []

    async def get_team_statistics(
        self, team_id: int, league_id: int, season: int
    ) -> dict:
        """Busca estatísticas agregadas de um time em uma liga/temporada.

        Usado pelo predictor para obter médias de escanteios, cartões, etc.

        Args:
            team_id: ID do time na API-Football.
            league_id: ID da liga.
            season: Temporada (ex: 2025).

        Returns:
            Dicionário com a resposta completa da API.
        """
        endpoint = "/teams/statistics"
        params = {
            "team": team_id,
            "league": league_id,
            "season": season,
        }

        try:
            from radar_ev.cache import cache
            cache_key = f"stats_{team_id}_{league_id}_{season}"
            
            # Tenta pegar do cache local
            cached_data = await cache.get(cache_key)
            if cached_data:
                logger.info(
                    "team_statistics_cache_hit",
                    team_id=team_id,
                    league_id=league_id,
                    season=season,
                )
                return cached_data

            # Se não estiver no cache, faz a requisição
            data = await self.client.get(endpoint, params=params)
            
            # Salva no cache por 72 horas
            if data and data.get("response"):
                await cache.set(cache_key, data, ttl_hours=72)
                
            logger.info(
                "team_statistics_fetched",
                team_id=team_id,
                league_id=league_id,
                season=season,
            )
            return data
        except Exception as exc:
            logger.error(
                "team_statistics_failed",
                team_id=team_id,
                error=str(exc),
            )
            return {}

    async def get_lineups(self, fixture_id: int) -> Optional[dict]:
        """Busca escalações confirmadas para uma partida (placeholder).

        Endpoint: /fixtures/lineups

        Nota: Ainda não integrado ao pipeline. Quando implementado,
        preencherá home_lineup_confirmed e away_lineup_confirmed no Match.

        Args:
            fixture_id: ID da partida.

        Returns:
            Dicionário com escalações ou None se não disponível.
        """
        endpoint = "/fixtures/lineups"
        params = {"fixture": fixture_id}

        try:
            data = await self.client.get(endpoint, params=params)
            response = data.get("response", [])
            if response:
                logger.info("lineups_fetched", fixture_id=fixture_id)
                return data
            return None
        except Exception as exc:
            logger.warning("lineups_fetch_failed", fixture_id=fixture_id, error=str(exc))
            return None

    async def close(self) -> None:
        """Fecha o cliente HTTP."""
        await self.client.close()

    async def __aenter__(self) -> "FootballAPICollector":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    @staticmethod
    def _normalize_market_name(bet_name: str, odd_label: str) -> str:
        """Normaliza o nome do mercado para formato padronizado.

        Converte nomes como "Over/Under" + "Over 9.5" em "corners_over_9.5".

        Args:
            bet_name: Nome do tipo de aposta (ex: "Corners Over/Under").
            odd_label: Label da odd específica (ex: "Over 9.5").

        Returns:
            Nome normalizado do mercado (ex: "corners_over_9.5").
        """
        # Normaliza para lowercase e remove espaços extras
        name = f"{bet_name}_{odd_label}".lower().strip()

        # Substitui espaços por underscore
        name = name.replace(" ", "_")

        # Remove caracteres especiais
        name = name.replace("/", "_")

        return name
