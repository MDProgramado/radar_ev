"""
test_orchestrator.py — Testes para o pipeline orquestrador.
"""

import pytest
from unittest.mock import AsyncMock, patch
from radar_ev.orchestrator import _filter_matches, _extract_threshold
from radar_ev.models import Match
from datetime import datetime, timedelta, timezone


class TestFilterMatches:
    def test_filters_by_league(self):
        now = datetime.now(timezone.utc)
        matches = [
            Match(id=1, home_team="A", away_team="B",
                  datetime=now + timedelta(hours=2),
                  league="Premier League"),
            Match(id=2, home_team="C", away_team="D",
                  datetime=now + timedelta(hours=2),
                  league="Liga Desconhecida"),
        ]
        filtered = _filter_matches(matches)
        assert len(filtered) == 1
        assert filtered[0].league == "Premier League"

    def test_filters_past_matches(self):
        now = datetime.now(timezone.utc)
        matches = [
            Match(id=1, home_team="A", away_team="B",
                  datetime=now - timedelta(hours=1),
                  league="Premier League"),
        ]
        filtered = _filter_matches(matches)
        assert len(filtered) == 0


class TestExtractThreshold:
    def test_extract_from_corners(self):
        assert _extract_threshold("corners_over_9.5") == 9.5

    def test_extract_from_cards(self):
        assert _extract_threshold("cards_over_4.5") == 4.5

    def test_default_value(self):
        assert _extract_threshold("unknown_market", 9.5) == 9.5

    def test_extract_integer(self):
        assert _extract_threshold("corners_over_10") == 10.0


@pytest.mark.asyncio
async def test_mock_pipeline():
    """Testa o pipeline com dados mockados."""
    from radar_ev.orchestrator import _run_mock_pipeline
    opps = await _run_mock_pipeline()
    assert isinstance(opps, list)
