"""
test_predictor.py — Testes para o modelo Poisson.
"""

from unittest.mock import AsyncMock

import pytest

from radar_ev.predictor import (
    DEFAULT_CORNERS_AVG,
    estimate_total_cards,
    estimate_total_corners,
    extract_cards_stats,
    extract_corners_stats,
    make_cards_prediction,
    make_corners_prediction,
    make_prediction_from_lambda,
    poisson_prob_exact,
    poisson_prob_over_k,
    poisson_prob_under_k,
)


class TestPoissonProbabilities:
    def test_over_k_reasonable(self):
        prob = poisson_prob_over_k(10.0, 9)
        assert 0.4 < prob < 0.7  # ~0.54 for λ=10

    def test_over_k_high_lambda(self):
        prob = poisson_prob_over_k(15.0, 9)
        assert prob > 0.9

    def test_over_k_low_lambda(self):
        prob = poisson_prob_over_k(5.0, 9)
        assert prob < 0.1

    def test_over_k_zero_lambda(self):
        assert poisson_prob_over_k(0.0, 5) == 0.0

    def test_under_k_complement(self):
        lam = 10.0
        over = poisson_prob_over_k(lam, 9)
        exact = poisson_prob_exact(lam, 10)
        # P(>9) + P(<=9) should ≈ 1
        under_eq = 1 - over
        assert abs(under_eq + over - 1.0) < 0.001

    def test_exact_probability(self):
        prob = poisson_prob_exact(10.0, 10)
        assert 0.1 < prob < 0.15  # ~0.125


class TestEstimates:
    def test_corners_simple(self):
        lam = estimate_total_corners(5.5, 4.5)
        assert lam == 10.5

    def test_corners_full_model(self):
        lam = estimate_total_corners(5.5, 4.5, 4.0, 5.0)
        assert lam > 0

    def test_cards_simple(self):
        lam = estimate_total_cards(2.5, 2.0)
        assert lam == 4.5

    def test_cards_with_referee(self):
        lam = estimate_total_cards(2.5, 2.0, 5.0)
        assert lam != 4.5  # Referee adjustment


class TestExtractStats:
    def test_extract_corners(self):
        data = {"response": {"corners": {
            "for": {"average": {"total": "5.8"}},
            "against": {"average": {"total": "4.2"}}
        }}}
        result = extract_corners_stats(data)
        assert result["avg_for"] == 5.8
        assert result["avg_against"] == 4.2

    def test_extract_corners_fallback(self):
        result = extract_corners_stats({})
        assert result["avg_for"] == DEFAULT_CORNERS_AVG

    def test_extract_cards(self):
        data = {"response": {
            "cards": {"yellow": {"0-15": {"total": 10}, "16-30": {"total": 15}},
                      "red": {"0-15": {"total": 1}, "16-30": {"total": 2}}},
            "fixtures": {"played": {"total": 10}}
        }}
        result = extract_cards_stats(data)
        assert result["avg_total"] > 0


class TestMakePrediction:
    def test_from_lambda(self):
        pred = make_prediction_from_lambda(1, "corners_over_9.5", 10.5, 9.5)
        assert 0.01 <= pred.probability <= 0.99
        assert pred.fair_odd > 0
        assert pred.model_version == "poisson_manual_v1"

    def test_high_lambda_high_prob(self):
        pred = make_prediction_from_lambda(1, "test", 15.0, 9.5)
        assert pred.probability > 0.8

    def test_low_lambda_low_prob(self):
        pred = make_prediction_from_lambda(1, "test", 5.0, 9.5)
        assert pred.probability < 0.2


class TestMakeCornerCardPredictions:
    """Regressão Bug 2/Bug 3: Over/Under em corners/cards e λ=0 em cartões."""

    @staticmethod
    def _stats(
        corners_for: str = "6.0",
        corners_against: str = "4.0",
        cards_total: int = 40,
        fixtures: int = 10,
    ) -> dict:
        return {"response": {
            "corners": {
                "for": {"average": {"total": corners_for}},
                "against": {"average": {"total": corners_against}},
            },
            "cards": {"yellow": {"0-90": {"total": cards_total}}, "red": {}},
            "fixtures": {"played": {"total": fixtures}},
        }}

    @staticmethod
    def _api(stats: dict) -> AsyncMock:
        api = AsyncMock()
        api.get_team_statistics = AsyncMock(return_value=stats)
        return api

    @pytest.mark.asyncio
    async def test_corners_under_is_complement_of_over(self):
        # Bug 2: P(Under 9.5) deve ser 1 − P(Over 9.5), não a prob do Over.
        api = self._api(self._stats())
        over = await make_corners_prediction(
            match_id=1, home_team_id=1, away_team_id=2,
            league_id=71, season=2026, football_api=api,
            threshold=9.5, is_over=True,
        )
        under = await make_corners_prediction(
            match_id=1, home_team_id=1, away_team_id=2,
            league_id=71, season=2026, football_api=api,
            threshold=9.5, is_over=False,
        )
        assert over.market == "corners_over_9.5"
        assert under.market == "corners_under_9.5"
        assert over.probability == pytest.approx(1.0 - under.probability, abs=0.01)
        assert under.probability + over.probability == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_cards_under_is_complement_of_over(self):
        # Bug 2 aplicado aos cartões.
        api = self._api(self._stats(cards_total=40))
        over = await make_cards_prediction(
            match_id=1, home_team_id=1, away_team_id=2,
            league_id=71, season=2026, football_api=api,
            threshold=4.5, is_over=True,
        )
        under = await make_cards_prediction(
            match_id=1, home_team_id=1, away_team_id=2,
            league_id=71, season=2026, football_api=api,
            threshold=4.5, is_over=False,
        )
        assert over is not None
        assert under is not None
        assert over.market == "cards_over_4.5"
        assert under.market == "cards_under_4.5"
        assert under.probability == pytest.approx(1.0 - over.probability, abs=0.01)

    @pytest.mark.asyncio
    async def test_cards_lambda_zero_returns_none(self, caplog):
        # Bug 3: λ=0 → None + warning, nunca p=0.01/fair_odd=100.
        api = self._api(self._stats(cards_total=0))
        with caplog.at_level("WARNING"):
            pred = await make_cards_prediction(
                match_id=1, home_team_id=1, away_team_id=2,
                league_id=71, season=2026, football_api=api,
                threshold=4.5,
            )
        assert pred is None
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("cards_lambda_zero" in r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_cards_lambda_positive_returns_prediction(self):
        api = self._api(self._stats(cards_total=40))
        pred = await make_cards_prediction(
            match_id=1, home_team_id=1, away_team_id=2,
            league_id=71, season=2026, football_api=api,
            threshold=4.5,
        )
        assert pred is not None
        assert pred.fair_odd > 1.0
        assert pred.model_version == "poisson_cards_v1"
