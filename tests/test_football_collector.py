"""
test_football_collector.py — Testes para o coletor da API-Football.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from radar_ev.collectors.football_api import FootballAPICollector
from radar_ev.http_client import ApiError, ApiPlanInsufficientError, ApiQuotaExhaustedError
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


@pytest.mark.asyncio
async def test_get_team_statistics_cached_in_memory():
    """2 chamadas para o mesmo time/liga/season → apenas 1 requisição HTTP."""
    mock_response = {
        "response": {
            "team": {"id": 10, "name": "Time A"},
            "league": {"id": 71},
            "goals": {
                "for": {"average": {"total": "1.8"}},
                "against": {"average": {"total": "1.2"}},
            },
        }
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector._stats_cache = {}
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        with patch("radar_ev.cache.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            first = await collector.get_team_statistics(10, 71, 2026)
            second = await collector.get_team_statistics(10, 71, 2026)

        assert first == mock_response
        assert second == mock_response
        assert collector.client.get.await_count == 1


@pytest.mark.asyncio
async def test_get_team_statistics_cached_even_with_empty_response():
    """Resposta vazia (sem key 'response') também é cacheada por execução."""
    empty_response = {"get": "/teams/statistics", "response": []}

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector._stats_cache = {}
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=empty_response)

        with patch("radar_ev.cache.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            first = await collector.get_team_statistics(99, 71, 2026)
            second = await collector.get_team_statistics(99, 71, 2026)

        assert first == empty_response
        assert second == empty_response
        assert collector.client.get.await_count == 1


@pytest.mark.asyncio
async def test_get_today_matches_raises_on_quota_exhausted():
    """API-Football responde 200 com errors preenchido (cota diária estourada)
    → get_today_matches deve levantar ApiQuotaExhaustedError em vez de
    retornar sucesso silencioso com 0 partidas."""
    mock_response = {
        "get": "/fixtures",
        "errors": {
            "requests": "Your account has reached the limit of request by day. Try again tomorrow."
        },
        "results": 0,
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        with patch("radar_ev.cache.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            with pytest.raises(ApiQuotaExhaustedError):
                await collector.get_today_matches(datetime(2025, 4, 2))


@pytest.mark.asyncio
async def test_get_today_matches_raises_on_plan_insufficient():
    """Errors com mensagem de plano ('Free plans do not have access...')
    → levanta ApiPlanInsufficientError (exit 3), NÃO quota."""
    mock_response = {
        "get": "/fixtures",
        "errors": {
            "plan": "Free plans do not have access to this season"
        },
        "results": 0,
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        with patch("radar_ev.cache.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            with pytest.raises(ApiPlanInsufficientError):
                await collector.get_today_matches(datetime(2025, 4, 2))


@pytest.mark.asyncio
async def test_get_today_matches_raises_on_generic_api_error():
    """Errors não-vazio sem palavra-chave de quota/plano
    → levanta ApiError genérico (exit 4), não trata como sucesso."""
    mock_response = {
        "get": "/fixtures",
        "errors": {
            "parameter": "Invalid value for parameter date"
        },
        "results": 0,
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        with patch("radar_ev.cache.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            with pytest.raises(ApiError) as excinfo:
                await collector.get_today_matches(datetime(2025, 4, 2))

        assert not isinstance(excinfo.value, ApiQuotaExhaustedError)
        assert not isinstance(excinfo.value, ApiPlanInsufficientError)


@pytest.mark.asyncio
async def test_get_today_matches_proceeds_with_empty_errors_field():
    """Resposta com ``errors`` vazio ({}) → nada a bloquear;
    o pipeline prossegue normalmente e parseia os matches."""
    mock_response = {
        "get": "/fixtures",
        "errors": {},
        "results": 1,
        "response": [{
            "fixture": {"id": 999, "date": "2025-04-02T20:00:00+00:00",
                        "referee": None},
            "teams": {"home": {"name": "Santos", "id": 134},
                      "away": {"name": "Palmeiras", "id": 135}},
            "league": {"name": "Brasileirão", "id": 71, "season": 2025}
        }]
    }

    with patch.object(FootballAPICollector, '__init__', lambda self: None):
        collector = FootballAPICollector()
        collector.client = MagicMock()
        collector.client.get = AsyncMock(return_value=mock_response)

        with patch("radar_ev.cache.cache") as mock_cache:
            mock_cache.get = AsyncMock(return_value=None)
            mock_cache.set = AsyncMock()

            matches = await collector.get_today_matches(datetime(2025, 4, 2))

        assert len(matches) == 1
        assert matches[0].home_team == "Santos"
        assert matches[0].away_team == "Palmeiras"
