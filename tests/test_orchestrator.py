"""
test_orchestrator.py — Testes para o pipeline orquestrador.
"""

import pytest
from unittest.mock import AsyncMock, patch
from radar_ev.orchestrator import (
    _filter_matches,
    _extract_threshold,
    _get_prediction_for_market,
    _parse_market,
)
from radar_ev.models import Match
from datetime import datetime, timedelta, timezone


def _match(**kwargs):
    """Cria um Match com IDs válidos, pronto para predição."""
    defaults = dict(
        id=1,
        home_team="Palmeiras",
        away_team="Corinthians",
        datetime=datetime.now(timezone.utc) + timedelta(hours=2),
        league="Serie A",
        home_team_id=101,
        away_team_id=102,
        league_id=71,
        season=2026,
    )
    defaults.update(kwargs)
    return Match(**defaults)


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


class TestParseMarketWhitelist:
    """Whitelist estrita de mercados (regex + range de sanity)."""

    def test_corners_over_95_accepted(self):
        assert _parse_market("corners_over_9.5") == ("corners", "over", 9.5)

    def test_goals_under_25_accepted(self):
        assert _parse_market("goals_under_2.5") == ("goals", "under", 2.5)

    def test_cards_over_45_accepted(self):
        assert _parse_market("cards_over_4.5") == ("cards", "over", 4.5)

    def test_corners_over_10_rejected_out_of_range(self):
        assert _parse_market("corners_over_1.0") is None

    def test_corners_over_200_rejected_out_of_range(self):
        assert _parse_market("corners_over_20.0") is None

    def test_odd_even_rejected(self):
        assert _parse_market("odd_even_even") is None

    def test_double_chance_rejected(self):
        assert _parse_market("double_chance_home_away") is None

    def test_asian_handicap_rejected(self):
        assert _parse_market("asian_handicap_away_-1") is None

    def test_first_half_team_goals_rejected(self):
        assert _parse_market("away_team_total_goals(1st_half)_under_1.5") is None

    def test_anytime_goal_scorer_rejected(self):
        assert _parse_market("home_anytime_goal_scorer_nicolas_jackson") is None

    def test_partial_market_rejected(self):
        assert _parse_market("corners_over") is None

    def test_three_decimals_rejected(self):
        assert _parse_market("corners_over_9.555") is None


class TestGetPredictionForMarket:
    """Mediação real do pipeline: rejeita antes de tocar a API, aceita e despacha."""

    @pytest.mark.asyncio
    async def test_rejected_market_returns_none_with_ids(self):
        pred = await _get_prediction_for_market(
            match=_match(),
            market="odd_even_even",
            football_api=AsyncMock(),
        )
        assert pred is None

    @pytest.mark.asyncio
    async def test_rejected_market_returns_none_without_api_call(self):
        with patch("radar_ev.orchestrator.make_corners_prediction") as mock_pred:
            pred = await _get_prediction_for_market(
                match=_match(),
                market="corners_over_1.0",  # fora do range
                football_api=AsyncMock(),
            )
            assert pred is None
            mock_pred.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepted_corners_dispatches_to_predictor(self):
        expected = object()
        with patch(
            "radar_ev.orchestrator.make_corners_prediction",
            new=AsyncMock(return_value=expected),
        ) as mock_pred:
            pred = await _get_prediction_for_market(
                match=_match(),
                market="corners_over_9.5",
                football_api=AsyncMock(),
            )
            assert pred is expected
            mock_pred.assert_awaited_once()
            _, kwargs = mock_pred.await_args
            assert kwargs["threshold"] == 9.5

    @pytest.mark.asyncio
    async def test_accepted_goals_under_dispatches_with_is_over_false(self):
        from radar_ev.models import Prediction

        fake_pred = Prediction(
            match_id=1,
            market="goals_under_2.5",
            probability=0.5,
            fair_odd=2.0,
            model_version="test",
        )
        with patch(
            "radar_ev.orchestrator.make_goals_prediction",
            new=AsyncMock(return_value=fake_pred),
        ) as mock_pred:
            pred = await _get_prediction_for_market(
                match=_match(),
                market="goals_under_2.5",
                football_api=AsyncMock(),
            )
            assert pred is fake_pred
            _, kwargs = mock_pred.await_args
            assert kwargs["is_over"] is False
            assert kwargs["threshold"] == 2.5
