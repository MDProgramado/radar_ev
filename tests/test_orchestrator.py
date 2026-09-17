"""
test_orchestrator.py — Testes para o pipeline orquestrador.
"""

import pytest
from unittest.mock import AsyncMock, patch
from radar_ev.collectors.football_api import FootballAPICollector
from radar_ev.ev_calculator import calculate_ev, calculate_ev_vig_removed
from radar_ev.models import Match, Odds, Prediction
from radar_ev.orchestrator import (
    _calculate_ev_without_vig,
    _complementary_odd,
    _filter_matches,
    _extract_threshold,
    _get_prediction_for_market,
    _parse_market,
    _report_fallback_alert,
)
from radar_ev.vig_stats import VigStats, vig_stats
from datetime import datetime, timedelta, timezone

# Roteia structlog para o logging padrão para permitir captura via caplog.
import structlog
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        structlog.processors.KeyValueRenderer(sort_keys=False),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)


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


class TestNormalizeMarketName:
    """Normalizador canônico (casos reais da Betano)."""

    norm = staticmethod(FootballAPICollector._normalize_market_name)

    def test_goals_over_25(self):
        assert self.norm("Goals Over/Under", "Over 2.5") == "goals_over_2.5"

    def test_goals_under_25(self):
        assert self.norm("Goals Over/Under", "Under 2.5") == "goals_under_2.5"

    def test_goals_over_under_first_half_rejected(self):
        assert self.norm("Goals Over/Under First Half", "Over 0.5") is None

    def test_corners_over_95(self):
        assert self.norm("Corners Over/Under", "Over 9.5") == "corners_over_9.5"

    def test_cards_over_45(self):
        assert self.norm("Cards Over/Under", "Over 4.5") == "cards_over_4.5"

    def test_cards_under_45(self):
        assert self.norm("Cards Over/Under", "Under 4.5") == "cards_under_4.5"

    def test_total_home_rejected(self):
        assert self.norm("Total - Home", "Under 2.5") is None

    def test_total_away_rejected(self):
        assert self.norm("Total - Away", "Under 1.5") is None

    def test_home_team_rejected(self):
        assert self.norm("Home Team Total Goals", "Over 1.5") is None

    def test_away_team_rejected(self):
        assert self.norm("Away Team Total Goals", "Under 1.5") is None

    def test_match_winner_rejected(self):
        assert self.norm("Match Winner", "Home") is None

    def test_asian_handicap_rejected(self):
        assert self.norm("Asian Handicap", "Home +0.25") is None

    def test_odd_even_rejected(self):
        assert self.norm("Odd/Even", "Even") is None

    def test_double_chance_rejected(self):
        assert self.norm("Double Chance", "Home/Draw") is None

    def test_home_away_bet_rejected(self):
        assert self.norm("Corners Home/Away", "Home") is None

    def test_home_corners_over_under_rejected(self):
        assert self.norm("Home Corners Over/Under", "Over 3.5") is None

    def test_away_corners_over_under_rejected(self):
        assert self.norm("Away Corners Over/Under", "Over 5.5") is None

    def test_home_team_goals_over_under_rejected(self):
        assert self.norm("Home Team Goals Over/Under", "Over 1.5") is None

    def test_home_cards_over_under_rejected(self):
        assert self.norm("Home Cards Over/Under", "Over 2.5") is None

    def test_total_corners_over_95(self):
        assert self.norm("Total Corners", "Over 9.5") == "corners_over_9.5"

    def test_corners_over_under_spaces(self):
        assert self.norm("Corners Over Under", "Over 9.5") == "corners_over_9.5"

    def test_no_direction_rejected(self):
        assert self.norm("Goals Over/Under", "Total 2.5") is None

    def test_no_line_rejected(self):
        assert self.norm("Goals Over/Under", "Over") is None

    def test_shot_on_target_rejected(self):
        assert self.norm("Shot On Target", "Over 4.5") is None


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


class TestVigFallback:
    """Integração: par Over/Under, fallback e limiares divergentes."""

    def _pred(self, prob: float = 0.55) -> Prediction:
        return Prediction(
            match_id=10,
            market="corners_over_9.5",
            probability=prob,
            fair_odd=round(1.0 / prob, 4),
            model_version="test",
        )

    def _odds(self, market: str, odd_value: float) -> Odds:
        return Odds(match_id=10, market=market, odd_value=odd_value)

    def test_pair_over_under_uses_vig_removed(self):
        vig_stats.reset()
        pred = self._pred(0.55)
        odds_list = [
            self._odds("corners_over_9.5", 2.10),
            self._odds("corners_under_9.5", 1.75),
        ]
        ev, vig_used = _calculate_ev_without_vig(pred, 2.10, "corners_over_9.5", odds_list)
        assert vig_used is True
        assert ev == pytest.approx(calculate_ev_vig_removed(pred, 2.10, 1.75))
        assert _complementary_odd("corners_over_9.5", odds_list) == 1.75
        assert vig_stats.with_vig == 1
        assert vig_stats.fallback == 0

    def test_without_pair_falls_back_and_logs_warning(self, caplog):
        vig_stats.reset()
        pred = self._pred(0.55)
        odds_list = [self._odds("corners_over_9.5", 2.10)]
        with caplog.at_level("WARNING"):
            ev, vig_used = _calculate_ev_without_vig(pred, 2.10, "corners_over_9.5", odds_list)
        assert vig_used is False
        assert ev == pytest.approx(calculate_ev(pred, 2.10))
        assert _complementary_odd("corners_over_9.5", odds_list) is None
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("complementary_odd_not_found" in r.getMessage() for r in warnings)
        assert any("match_id=10" in r.getMessage() for r in warnings)
        assert vig_stats.fallback == 1

    def test_mismatched_line_is_treated_as_no_pair(self, caplog):
        # Definido: par exige a MESMA linha (over_9.5 + under_10.5 ≠ par).
        # Limiares diferentes → sem complemento → fallback para odd bruta.
        vig_stats.reset()
        pred = self._pred(0.55)
        odds_list = [
            self._odds("corners_over_9.5", 2.10),
            self._odds("corners_under_10.5", 1.75),
        ]
        with caplog.at_level("WARNING"):
            ev, vig_used = _calculate_ev_without_vig(pred, 2.10, "corners_over_9.5", odds_list)
        assert vig_used is False
        assert ev == pytest.approx(calculate_ev(pred, 2.10))
        assert _complementary_odd("corners_over_9.5", odds_list) is None
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("complementary_odd_not_found" in r.getMessage() for r in warnings)


class TestVigFallbackStats:
    """Contadores: % de oportunidades com vig removido vs. fallback."""

    def _pred(self) -> Prediction:
        return Prediction(
            match_id=1,
            market="corners_over_9.5",
            probability=0.55,
            fair_odd=1.82,
            model_version="test",
        )

    def test_five_opportunities_three_pairs(self):
        # 5 avaliações → 3 com par Over/Under, 2 sem → 60% de sucesso.
        vig_stats.reset()
        pair = [
            Odds(match_id=1, market="corners_over_9.5", odd_value=1.90),
            Odds(match_id=1, market="corners_under_9.5", odd_value=1.90),
        ]
        lone = [Odds(match_id=1, market="corners_over_9.5", odd_value=1.90)]
        pred = self._pred()

        for odds_list in [pair, pair, pair, lone, lone]:
            _calculate_ev_without_vig(pred, 1.90, "corners_over_9.5", odds_list)

        assert vig_stats.with_vig == 3
        assert vig_stats.fallback == 2
        assert vig_stats.total == 5
        assert vig_stats.vig_percent == pytest.approx(60.0)
        assert vig_stats.fallback_percent == pytest.approx(40.0)

    def test_alert_when_fallback_above_threshold(self, caplog):
        stats = VigStats()
        stats.with_vig = 3
        stats.fallback = 5  # 62.5% de fallback > 20%
        with caplog.at_level("ERROR"):
            message = _report_fallback_alert(stats)
        assert message is not None
        assert "ALERTA:" in message
        assert "62.5%" in message
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert any("vig_fallback_alert" in r.getMessage() for r in errors)

    def test_no_alert_below_threshold(self):
        stats = VigStats()
        stats.with_vig = 5
        stats.fallback = 1  # 16.7% < 20%
        assert _report_fallback_alert(stats) is None

    def test_no_alert_when_no_evaluations(self):
        stats = VigStats()
        assert _report_fallback_alert(stats) is None
