"""
test_predictor.py — Testes para o modelo Poisson.
"""

import pytest
from radar_ev.predictor import (
    poisson_prob_over_k, poisson_prob_under_k, poisson_prob_exact,
    estimate_total_corners, estimate_total_cards,
    extract_corners_stats, extract_cards_stats,
    make_prediction_from_lambda, DEFAULT_CORNERS_AVG,
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
