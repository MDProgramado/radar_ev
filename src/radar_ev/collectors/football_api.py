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
import re
from typing import List, Optional

import structlog

from radar_ev.config import settings
from radar_ev.http_client import (
    ApiError,
    HTTPClient,
    RateLimitError,
    raise_for_api_errors,
)
from radar_ev.models import Match, Odds

logger = structlog.get_logger(__name__)


def _check_api_errors(data: dict, endpoint: str) -> None:
    """Classifica e levanta erros de API-Football vindos no corpo do JSON.

    A API-Football responde HTTP 200 com ``results: 0`` e campo ``errors``
    quando a cota diária esgota (ex: 442 partidas viraram 0 em silêncio),
    quando o plano não concede acesso, ou para outros erros. Delega para o
    classificador central (http_client.raise_for_api_errors), que distingue:
    quota (exit 2), plano insuficiente (exit 3) e erro genérico (exit 4).
    """
    raise_for_api_errors(data, endpoint)


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
        # Cache em memória por execução (não persiste entre execuções):
        # evita chamadas repetidas de /teams/statistics para o mesmo
        # (team_id, league_id, season).
        self._stats_cache: dict[tuple[int, int, int], dict] = {}

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

        # Usa cache para fixtures (salva por 4 horas para economizar limites da API)
        from radar_ev.cache import cache
        cache_key = f"fixtures_{params['date']}"
        data = await cache.get(cache_key)
        
        if not data:
            data = await self.client.get(endpoint, params=params)
            if data and data.get("response"):
                await cache.set(cache_key, data, ttl_hours=4)

        # Cota/plano/erro de API: API-Football responde 200 com results:0 e
        # errors preenchido. NÃO é sucesso silencioso — propaga para o
        # orquestrador encerrar com código próprio (2/3/4).
        _check_api_errors(data, endpoint)

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

        return matches

    async def get_odds(
        self,
        fixture_id: int,
        bookmakers: Optional[List[str]] = None,
    ) -> List[Odds]:
        """Busca odds de uma partida via /odds para uma ou mais casas.

        Por padrão coleta da Betano E da Pinnacle. A Pinnacle tem margem
        ~2% (Betano ~5-8%) e serve como referência de "odd justa" do mercado:
        comparar a odd capturada com a Pinnacle permite detectar value betting
        sem depender de estatísticas de time. Cada Odds carrega ``offered_by``
        com o nome (lowercase) da casa — o campo ``source``.

        Se nenhuma das casas configuradas estiver disponível, retorna lista
        vazia (esperado para muitas ligas).

        Args:
            fixture_id: ID da partida na API-Football.
            bookmakers: Casas a incluir (lowercase). Default: betano+pinnacle.

        Returns:
            Lista de Odds de todas as casas configuradas, com ``offered_by``
            indicando a fonte (betano/pinnacle).
        """
        if bookmakers is None:
            bookmakers = ["betano", "pinnacle"]

        endpoint = "/odds"
        params = {"fixture": fixture_id}
        bookmaker_set = {name.lower() for name in bookmakers}

        try:
            data = await self.client.get(endpoint, params=params)
            _check_api_errors(data, endpoint)
            odds_list: List[Odds] = []

            for fixture_data in data.get("response", []):
                for bookmaker in fixture_data.get("bookmakers", []):
                    bookmaker_name = bookmaker.get("name", "").lower()
                    # Filtra apenas pelas casas configuradas
                    if bookmaker_name not in bookmaker_set:
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
                                    # Nome do mercado: "tipo_over/under_linha" (ex: "goals_over_2.5")
                                    market_name = self._normalize_market_name(bet_name, str(odd_label))
                                    if market_name is None:
                                        continue

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

        except ApiError:
            raise  # Erro de API classificado (quota/plano/genérico) — propaga

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

        IMPORTANTE: a temporada efetivamente consultada é ``settings.season``,
        NÃO o parâmetro ``season``. O plano Free da API-Football bloqueia a
        temporada atual (ex: 2026) com 200 + errors {plan: ...}; as fixtures
        trazem ``league.season`` = temporada corrente, mas as estatísticas só
        existem para temporadas antigas no Free (até 2024).

        Args:
            team_id: ID do time na API-Football.
            league_id: ID da liga.
            season: Temporada "desejada" (mantida para compatibilidade de
                assinatura; a requisição usa settings.season).

        Returns:
            Dicionário com a resposta completa da API.
        """
        effective_season = settings.season
        if effective_season != season:
            logger.info(
                "team_statistics_season_override",
                requested_season=season,
                effective_season=effective_season,
                reason="plano Free bloqueia temporadas recentes (2025/2026)",
            )
        endpoint = "/teams/statistics"
        params = {
            "team": team_id,
            "league": league_id,
            "season": effective_season,
        }

        try:
            from radar_ev.cache import cache
            cache_key = f"stats_{team_id}_{league_id}_{effective_season}"
            memory_key = (team_id, league_id, effective_season)

            # 1) Cache em memória (desta execução): evita chamadas repetidas
            #    de /teams/statistics para o mesmo time/liga/temporada.
            #    Guarda TUDO o que retornou (mesmo respostas vazias/falhas)
            #    para não re-bater no endpoint várias vezes no mesmo run.
            if memory_key in self._stats_cache:
                logger.info(
                    "team_statistics_memory_cache_hit",
                    team_id=team_id,
                    league_id=league_id,
                    season=effective_season,
                )
                return self._stats_cache[memory_key]

            # 2) Cache em disco (SQLite local) — persiste entre execuções
            cached_data = await cache.get(cache_key)
            if cached_data:
                logger.info(
                    "team_statistics_cache_hit",
                    team_id=team_id,
                    league_id=league_id,
                    season=effective_season,
                )
                self._stats_cache[memory_key] = cached_data
                return cached_data

            # 3) Sem cache: faz a requisição real à API
            data = await self.client.get(endpoint, params=params)
            _check_api_errors(data, endpoint)

            # Salva em memória SEMPRE; em disco só se houver resposta útil
            self._stats_cache[memory_key] = data
            if data and data.get("response"):
                await cache.set(cache_key, data, ttl_hours=72)

            logger.info(
                "team_statistics_fetched",
                team_id=team_id,
                league_id=league_id,
                season=season,
            )
            return data
        except ApiError:
            raise  # Erro de API classificado (quota/plano/genérico) — propaga

        except Exception as exc:
            logger.error(
                "team_statistics_failed",
                team_id=team_id,
                error=str(exc),
            )
            # Evita repetir a chamada que falhou (ex: rate limit) no mesmo run
            self._stats_cache[memory_key] = {}
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
            _check_api_errors(data, endpoint)
            response = data.get("response", [])
            if response:
                logger.info("lineups_fetched", fixture_id=fixture_id)
                return data
            return None
        except ApiError:
            raise  # Erro de API classificado (quota/plano/genérico) — propaga
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
    def _normalize_market_name(bet_name: str, odd_label: str) -> Optional[str]:
        """Normaliza o nome do mercado para formato canônico.

        Converte nomes como "Goals Over/Under" + "Over 2.5" em "goals_over_2.5".

        Regras:
        - Categoria detectada a partir do bet_name (corner→corners, card→cards, goal→goals).
          Sem categoria conhecida, retorna None.
        - Variantes inválidas são rejeitadas explicitamente (1º/2º tempo,
          times específicos, Home/Away sem ser over/under).
        - Direção (over/under) vem do odd_label. Sem direção, retorna None.
        - Linha extraída via regex \\d+(?:\\.\\d{1,2})? do odd_label.
        - Sem linha, retorna None.

        Args:
            bet_name: Nome do tipo de aposta (ex: "Goals Over/Under").
            odd_label: Label da odd específica (ex: "Over 2.5").

        Returns:
            Nome canônico do mercado (ex: "goals_over_2.5") ou None se não suportado.
        """
        bname = bet_name.lower().strip()
        label = odd_label.lower().strip()

        # 1. Normaliza separadores: [_/\-.] viram espaço e múltiplos espaços colapsam
        normalized_bname = re.sub(r"\s+", " ", re.sub(r"[_/\-.]", " ", bname)).strip()

        # 2. Mercados específicos de TIME (Home/Away) não são suportados —
        #    o modelo só cobre a PARTIDA INTEIRA Over/Under
        if re.search(r"\b(home|away)\b", normalized_bname):
            return None

        # 3. Detecta categoria a partir do bet_name
        if "corner" in bname:
            category = "corners"
        elif "card" in bname:
            category = "cards"
        elif "goal" in bname:
            category = "goals"
        else:
            return None

        # 4. Variantes inválidas — apenas a PARTIDA INTEIRA Over/Under é aceita
        invalid_markers = [
            "first half",
            "1st half",
            "second half",
            "2nd half",
        ]
        if any(marker in normalized_bname for marker in invalid_markers):
            return None

        # 5+6. Direção e linha a partir do odd_label
        if "over" in label:
            direction = "over"
        elif "under" in label:
            direction = "under"
        else:
            return None

        match = re.search(r"\d+(?:\.\d{1,2})?", label)
        if not match:
            return None
        line = match.group(0)

        return f"{category}_{direction}_{line}"
