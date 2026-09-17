"""
test_ev_calculator.py — Testes para o cálculo de valor esperado.
"""

import pytest
from radar_ev.ev_calculator import (
    calculate_ev,
    calculate_ev_vig_removed,
    calculate_kelly,
    create_opportunity,
    remove_vig,
)
from radar_ev.models import Match, Odds, Prediction
from datetime import datetime, timedelta, timezone


class TestRemoveVig:
    """Remoção do overround (margem da casa) — método proporcional."""

    def test_symmetric_pair_sums_to_one(self):
        # 1.90 / 1.90 → margem de ~5.26% → 50%/50%
        probs = remove_vig([1.90, 1.90])
        assert abs(sum(probs) - 1.0) < 1e-9
        assert abs(probs[0] - 0.50) < 1e-9
        assert abs(probs[1] - 0.50) < 1e-9

    def test_asymmetric_pair_sums_to_one(self):
        # 2.10 / 1.75 → p_justas devem somar exatamente 1.0
        probs = remove_vig([2.10, 1.75])
        assert abs(sum(probs) - 1.0) < 1e-9
        assert probs[0] < probs[1]  # odd maior (2.10) → p menor

    def test_favorites_has_higher_fair_prob(self):
        # 1.50 (favorito) / 2.50 (azarão) → p_justa_over > p_justa_under
        probs = remove_vig([1.50, 2.50])
        assert probs[0] > probs[1]

    def test_fair_odd_larger_than_raw_odd(self):
        # A remoção de vig só AUMENTA a odd (a margem encolhe o preço)
        probs = remove_vig([1.90, 2.10])
        assert 1.0 / probs[0] > 1.90
        assert 1.0 / probs[1] > 2.10

    def test_unsupported_method_raises(self):
        import pytest

        with pytest.raises(ValueError):
            remove_vig([1.90, 1.90], method="shin")


class TestEvVigRemoved:
    """EV com odd justa vs. EV com odd bruta."""

    def _pred(self, prob: float = 0.55) -> Prediction:
        return Prediction(
            match_id=1, market="corners_over_9.5",
            probability=prob, fair_odd=1.0 / prob, model_version="poisson_v1",
        )

    def test_ev_without_vig_is_not_below_raw_ev(self):
        # p=0.55, Over 9.5 = 1.90, Under 9.5 = 1.90
        # EV_bruto = (0.55*1.90 − 1)*100 = 4.50%
        # EV_justo = (0.55*2.00 − 1)*100 = 10.00%  (vig removido)
        pred = self._pred(0.55)
        ev_raw = calculate_ev(pred, 1.90)
        ev_fair = calculate_ev_vig_removed(pred, 1.90, 1.90)
        assert abs(ev_raw - 4.50) < 0.01
        assert abs(ev_fair - 10.00) < 0.01
        assert ev_fair > ev_raw  # soberestimado não: subestimado sem remoção

    def test_three_outcomes_sums_to_one(self):
        # Mercado de 3 resultados (genérico, ex: 1X2) — soma = 1.0
        probs = remove_vig([2.10, 3.40, 3.60])
        assert abs(sum(probs) - 1.0) < 1e-9


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

    def test_vig_removed_propagates_to_opportunity(
        self, sample_match, sample_odds, sample_prediction
    ):
        opp = create_opportunity(
            sample_match, sample_prediction, sample_odds, 13.5, vig_removed=True
        )
        assert opp.vig_removed is True
        opp_fallback = create_opportunity(
            sample_match, sample_prediction, sample_odds, 13.5, vig_removed=False
        )
        assert opp_fallback.vig_removed is False

    def test_vig_removed_defaults_to_none(
        self, sample_match, sample_odds, sample_prediction
    ):
        opp = create_opportunity(sample_match, sample_prediction, sample_odds, 13.5)
        assert opp.vig_removed is None


class TestKelly:
    """Stake via Critério de Kelly Fracionário (1/4 Kelly)."""

    def test_quarter_kelly_stake_with_banca_1000(self):
        # p=0.55, odd=2.10, b=1.10
        # f_full = (1.10×0.55 − 0.45)/1.10 ≈ 0.14091
        # f_quarter = 0.14091 × 0.25 ≈ 0.03523 → 3.52% da banca
        percent = calculate_kelly(0.55, 2.10)
        assert percent == pytest.approx(3.52, abs=0.01)
        stake = percent / 100.0 * 1000.0
        assert stake == pytest.approx(35.23, abs=0.01)

    def test_default_uses_quarter_kelly(self):
        from radar_ev.config import settings

        explicit = calculate_kelly(0.55, 2.10, fraction=0.25)
        default = calculate_kelly(0.55, 2.10)
        assert settings.kelly_fraction == 0.25
        assert default == pytest.approx(explicit)

    def test_full_kelly_is_four_times_quarter(self):
        # Abaixo do cap de 5%: p=0.51, odd=2.00 → f_full=2.00%, f_quarter=0.50%
        full = calculate_kelly(0.51, 2.00, fraction=1.0)
        quarter = calculate_kelly(0.51, 2.00, fraction=0.25)
        assert full == pytest.approx(quarter * 4.0)
        assert full == pytest.approx(2.00)
        assert quarter == pytest.approx(0.50)

    def test_negative_value_returns_zero(self):
        assert calculate_kelly(0.30, 1.10) == 0.0

    def test_bad_odd_returns_zero(self):
        assert calculate_kelly(0.55, 1.0) == 0.0
