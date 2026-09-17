"""
test_ev_calculator.py — Testes para o cálculo de valor esperado.
"""

import pytest
from radar_ev.ev_calculator import calculate_ev, create_opportunity
from radar_ev.models import Match, Odds, Prediction
from datetime import datetime, timedelta, timezone


class TestCalculateEV:
    def test_positive_ev(self):
        # prob = 0.60 (60% de bater), odd = 2.00 (esperado 1.66)
        # EV = (0.60 * 2.00) - 1.0 = 0.20 = 20%
        pred = Prediction(match_id=1, market="corners", probability=0.60,
                          fair_odd=1.66, model_version="v1")
        ev = calculate_ev(pred, 2.00)
        assert ev > 0  # 20.0 > 0

    def test_negative_ev(self):
        # prob = 0.40, odd = 2.00
        # EV = (0.40 * 2.00) - 1.0 = -0.20 = -20%
        pred = Prediction(match_id=1, market="corners", probability=0.40,
                          fair_odd=2.50, model_version="v1")
        ev = calculate_ev(pred, 2.00)
        assert ev < 0

    def test_zero_ev(self):
        # prob = 0.50, odd = 2.00
        # EV = (0.50 * 2.00) - 1.0 = 0
        pred = Prediction(match_id=1, market="corners", probability=0.50,
                          fair_odd=2.00, model_version="v1")
        ev = calculate_ev(pred, 2.00)
        assert abs(ev) < 0.01  # ~0%

    def test_formula_correctness(self):
        pred = Prediction(match_id=1, market="test", probability=0.5,
                          fair_odd=2.00, model_version="v1")
        ev = calculate_ev(pred, 2.50)
        expected = (0.5 * 2.50 - 1.0) * 100
        assert abs(ev - expected) < 0.01

    def test_zero_offered_odd(self):
        pred = Prediction(match_id=1, market="test", probability=0.5,
                          fair_odd=2.0, model_version="v1")
        ev = calculate_ev(pred, 0)
        assert ev == -100.0


class TestCreateOpportunity:
    def test_creates_opportunity(self, sample_match, sample_odds, sample_prediction):
        opp = create_opportunity(sample_match, sample_prediction, sample_odds, 13.5)
        assert opp.ev_percent == 13.5
        assert opp.match.home_team == "Palmeiras"
        assert opp.market == "corners_over_9.5"
        assert opp.confidence == sample_prediction.probability
