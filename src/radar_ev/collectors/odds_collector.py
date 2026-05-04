"""
odds_collector.py — Cliente da The Odds API (DEPRECIADO).

Este módulo foi usado para coletar odds da The Odds API, mas causou problemas
de parâmetros (região inválida) e foi substituído pela chamada direta a /odds
da API-Football no módulo football_api.py.

Mantido apenas para referência e compatibilidade. NÃO é importado no orquestrador.

Status: DEPRECIADO — Usar football_api.get_odds() em vez deste módulo.
"""

import warnings
from datetime import datetime, timezone
from typing import List

from radar_ev.config import settings
from radar_ev.http_client import HTTPClient
from radar_ev.models import Odds

# Emite aviso se importado
warnings.warn(
    "odds_collector.py está depreciado. Use football_api.get_odds() em vez disso.",
    DeprecationWarning,
    stacklevel=2,
)


class OddsCollector:
    """Cliente da The Odds API (DEPRECIADO).

    Não deve ser usado em código novo. Mantido por referência.
    Use FootballAPICollector.get_odds() em vez disso.
    """

    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self) -> None:
        warnings.warn(
            "OddsCollector está depreciado. Use FootballAPICollector.get_odds().",
            DeprecationWarning,
            stacklevel=2,
        )
        self.client = HTTPClient(base_url=self.BASE_URL, timeout=30.0)

    async def get_odds_for_match(
        self, match_id: str, sport_key: str
    ) -> List[Odds]:
        """Busca odds para uma partida (DEPRECIADO)."""
        endpoint = f"/sports/{sport_key}/events/{match_id}/odds"
        params = {
            "apiKey": settings.odds_api_key,
            "regions": "br",
            "markets": "h2h,overunder",
            "oddsFormat": "decimal",
        }
        data = await self.client.get(endpoint, params=params)

        odds_list: List[Odds] = []
        for bookmaker in data.get("bookmakers", []):
            if bookmaker.get("title") == "Betano":
                for market in bookmaker.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        odds_list.append(
                            Odds(
                                match_id=int(match_id),
                                market=f"{market['key']}_{outcome['name']}",
                                odd_value=outcome["price"],
                                offered_by="betano",
                                timestamp=datetime.now(timezone.utc),
                            )
                        )
        return odds_list

    async def get_sports(self) -> list:
        """Lista esportes disponíveis (DEPRECIADO)."""
        endpoint = "/sports"
        params = {"apiKey": settings.odds_api_key}
        return await self.client.get(endpoint, params=params)

    async def close(self) -> None:
        """Fecha o cliente HTTP."""
        await self.client.close()
