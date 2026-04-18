import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from radar_ev.collectors.football_api import FootballAPICollector
from radar_ev.models import Match

@pytest.mark.asyncio
async def test_get_today_matches():
    # Mock da resposta da API
    mock_response = {
        "response": [
            {
                "fixture": {
                    "id": 123456,
                    "date": "2025-04-02T20:00:00+00:00",
                    "referee": "Wilton Sampaio"
                },
                "teams": {
                    "home": {"name": "Flamengo"},
                    "away": {"name": "Fluminense"}
                },
                "league": {"name": "Brasileirão"}
            }
        ]
    }

    with patch.object(FootballAPICollector, 'get_today_matches', new_callable=AsyncMock) as mock_method:
        mock_method.return_value = [
            Match(
                id=123456,
                home_team="Flamengo",
                away_team="Fluminense",
                datetime=datetime(2025, 4, 2, 20, 0),
                league="Brasileirão",
                referee="Wilton Sampaio"
            )
        ]
        collector = FootballAPICollector()
        matches = await collector.get_today_matches(datetime(2025, 4, 2))
        assert len(matches) == 1
        assert matches[0].home_team == "Flamengo"
        assert matches[0].away_team == "Fluminense"
