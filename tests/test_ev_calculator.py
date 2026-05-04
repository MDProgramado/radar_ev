"""
test_ev_calculator.py — Testes para o cálculo de valor esperado.
"""

import pytest
from radar_ev.ev_calculator import calculate_ev, create_opportunity
from radar_ev.models import Match, Odds, Prediction
from datetime import datetime, timedelta, timezone


class TestCalculateEV:
    def test_positive_ev(self):
        pred = Prediction(match_id=1, market="corners", probability=0.48,
                          fair_odd=2.08, model_version="v1")
        ev = calculate_ev(pred, 1.85)
        assert ev > 0  # 2.08 > 1.85

    def test_negative_ev(self):
        pred = Prediction(match_id=1, market="corners", probability=0.58,
                          fair_odd=1.72, model_version="v1")
        ev = calculate_ev(pred, 1.85)
        assert ev < 0  # 1.72 < 1.85

    def test_zero_ev(self):
        pred = Prediction(match_id=1, market="corners", probability=0.54,
                          fair_odd=1.85, model_version="v1")
        ev = calculate_ev(pred, 1.85)
        assert abs(ev) < 0.01  # ~0%

    def test_formula_correctness(self):
        pred = Prediction(match_id=1, market="test", probability=0.5,
                          fair_odd=2.10, model_version="v1")
        ev = calculate_ev(pred, 1.85)
        expected = (2.10 - 1.85) / 1.85 * 100
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
