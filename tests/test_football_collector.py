"""
test_football_collector.py — Testes para o coletor da API-Football.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from radar_ev.collectors.football_api import FootballAPICollector
from radar_ev.models import Match


@pytest.mark.asyncio
async def test_get_today_matches_parsing():
    """Testa parsing de resposta da API em objetos Match."""
    mock_response = {
        "response": [{
            "fixture": {"id": 123456, "date": "2025-04-02T20:00:00+00:00",
                        "referee": "Wilton Sampaio"},
            "teams": {"home": {"name": "Flamengo", "id": 127},
                      "away": {"name": "Fluminense", "id": 124}},
            "league": {"name": "Brasileirão", "id": 71, "season": 2025}
        }]
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        matches = await collector.get_today_matches(datetime(2025, 4, 2))
        assert len(matches) == 1
        assert matches[0].home_team == "Flamengo"
        assert matches[0].away_team == "Fluminense"
        assert matches[0].home_team_id == 127
        assert matches[0].league_id == 71


@pytest.mark.asyncio
async def test_get_odds_betano_filter():
    """Testa que apenas odds da Betano são retornadas."""
    mock_response = {
        "response": [{
            "bookmakers": [
                {"name": "Betano", "bets": [
                    {"name": "Corners", "values": [
                        {"value": "Over 9.5", "odd": "1.85"}
                    ]}
                ]},
                {"name": "Bet365", "bets": [
                    {"name": "Corners", "values": [
                        {"value": "Over 9.5", "odd": "1.90"}
                    ]}
                ]}
            ]
        }]
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        odds = await collector.get_odds(123456)
        assert len(odds) == 1
        assert odds[0].offered_by == "betano"
        assert odds[0].odd_value == 1.85


@pytest.mark.asyncio
async def test_get_odds_no_betano():
    """Testa resposta vazia quando Betano não está disponível."""
    mock_response = {
        "response": [{
            "bookmakers": [
                {"name": "Bet365", "bets": []}
            ]
        }]
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        odds = await collector.get_odds(123456)
        assert len(odds) == 0


@pytest.mark.asyncio
async def test_normalize_market_name():
    """Testa normalização de nomes de mercado."""
    name = FootballAPICollector._normalize_market_name("Corners Over/Under", "Over 9.5")
    assert "corners" in name
    assert "9.5" in name
    assert " " not in name
