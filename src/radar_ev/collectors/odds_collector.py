from datetime import datetime
from typing import List
from radar_ev.config import settings
from radar_ev.http_client import HTTPClient
from radar_ev.models import Odds

class OddsCollector:
    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self):
        self.client = HTTPClient(base_url=self.BASE_URL, timeout=30.0)

    async def get_odds_for_match(self, match_id: str, sport_key: str) -> List[Odds]:
        endpoint = f"/sports/{sport_key}/events/{match_id}/odds"
        params = {
            "apiKey": settings.odds_api_key,
            "regions": "br",
            "markets": "h2h,overunder",
            "oddsFormat": "decimal"
        }
        data = await self.client.get(endpoint, params=params)

        odds_list = []
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
                                timestamp=datetime.utcnow()
                            )
                        )
        return odds_list

    async def get_sports(self) -> List[dict]:
        endpoint = "/sports"
        params = {"apiKey": settings.odds_api_key}
        return await self.client.get(endpoint, params=params)

    async def close(self):
        await self.client.close()
