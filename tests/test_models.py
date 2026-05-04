"""
test_models.py — Testes para as entidades de domínio.

Verifica:
- Criação válida de Match, Odds, Prediction, Opportunity.
- Validação de campos obrigatórios e limites.
- Serialização e desserialização.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from radar_ev.models import Match, Odds, Opportunity, Prediction


class TestMatch:
    """Testes para o modelo Match."""

    def test_create_valid_match(self, sample_match):
        """Cria um Match válido."""
        assert sample_match.id == 1001
        assert sample_match.home_team == "Palmeiras"
        assert sample_match.away_team == "Corinthians"

    def test_match_with_team_ids(self, sample_match):
        """Verifica que IDs de time/liga/temporada são preenchidos."""
        assert sample_match.home_team_id == 121
        assert sample_match.away_team_id == 131
        assert sample_match.league_id == 71
        assert sample_match.season == 2025

    def test_match_optional_fields(self):
        """Verifica valores padrão dos campos opcionais."""
        match = Match(
            id=1,
            home_team="A",
            away_team="B",
            datetime=datetime.now(timezone.utc),
            league="Test",
        )
        assert match.referee is None
        assert match.home_lineup_confirmed is False
        assert match.away_lineup_confirmed is False
        assert match.motivation_index == 0.0
        assert match.home_team_id is None

    def test_motivation_index_bounds(self):
        """Verifica que motivation_index respeita limites 0-10."""
        with pytest.raises(ValidationError):
            Match(
                id=1,
                home_team="A",
                away_team="B",
                datetime=datetime.now(timezone.utc),
                league="Test",
                motivation_index=11.0,  # Acima do limite
            )

    def test_motivation_index_negative(self):
        """Verifica que motivation_index não aceita negativo."""
        with pytest.raises(ValidationError):
            Match(
                id=1,
                home_team="A",
                away_team="B",
                datetime=datetime.now(timezone.utc),
                league="Test",
                motivation_index=-1.0,
            )

    def test_match_serialization(self, sample_match):
        """Verifica serialização para dicionário."""
        data = sample_match.model_dump()
        assert data["id"] == 1001
        assert data["home_team"] == "Palmeiras"
        assert isinstance(data["datetime"], datetime)


class TestOdds:
    """Testes para o modelo Odds."""

    def test_create_valid_odds(self, sample_odds):
        """Cria um Odds válido."""
        assert sample_odds.match_id == 1001
        assert sample_odds.market == "corners_over_9.5"
        assert sample_odds.odd_value == 1.85

    def test_odds_value_must_be_positive(self):
        """Verifica que odd_value deve ser > 0."""
        with pytest.raises(ValidationError):
            Odds(match_id=1, market="test", odd_value=0)

    def test_odds_value_negative(self):
        """Verifica que odd_value não aceita negativo."""
        with pytest.raises(ValidationError):
            Odds(match_id=1, market="test", odd_value=-1.5)

    def test_odds_default_bookmaker(self):
        """Verifica que o bookmaker padrão é Betano."""
        odds = Odds(match_id=1, market="test", odd_value=1.5)
        assert odds.offered_by == "betano"

    def test_odds_timestamp_auto(self):
        """Verifica que o timestamp é gerado automaticamente."""
        odds = Odds(match_id=1, market="test", odd_value=1.5)
        assert odds.timestamp is not None
        assert odds.timestamp.tzinfo is not None  # Timezone-aware


class TestPrediction:
    """Testes para o modelo Prediction."""

    def test_create_valid_prediction(self, sample_prediction):
        """Cria uma Prediction válida."""
        assert sample_prediction.match_id == 1001
        assert sample_prediction.probability == 0.58
        assert sample_prediction.fair_odd == 1.72

    def test_probability_bounds(self):
        """Verifica que probability respeita limites 0-1."""
        with pytest.raises(ValidationError):
            Prediction(
                match_id=1,
                market="test",
                probability=1.5,  # Acima do limite
                fair_odd=0.67,
                model_version="v1",
            )

    def test_probability_negative(self):
        """Verifica que probability não aceita negativo."""
        with pytest.raises(ValidationError):
            Prediction(
                match_id=1,
                market="test",
                probability=-0.1,
                fair_odd=10.0,
                model_version="v1",
            )

    def test_fair_odd_must_be_positive(self):
        """Verifica que fair_odd deve ser > 0."""
        with pytest.raises(ValidationError):
            Prediction(
                match_id=1,
                market="test",
                probability=0.5,
                fair_odd=0,
                model_version="v1",
            )


class TestOpportunity:
    """Testes para o modelo Opportunity."""

    def test_create_valid_opportunity(self, sample_opportunity):
        """Cria uma Opportunity válida."""
        assert sample_opportunity.ev_percent == 13.5
        assert sample_opportunity.confidence == 0.72

    def test_opportunity_summary(self, sample_opportunity):
        """Verifica a propriedade summary."""
        summary = sample_opportunity.summary
        assert "Palmeiras" in summary
        assert "Corinthians" in summary
        assert "13.5%" in summary

    def test_confidence_bounds(self, sample_match):
        """Verifica que confidence respeita limites 0-1."""
        with pytest.raises(ValidationError):
            Opportunity(
                match=sample_match,
                market="test",
                fair_odd=2.0,
                offered_odd=1.8,
                ev_percent=11.0,
                confidence=1.5,  # Acima do limite
                reasoning="test",
            )

    def test_opportunity_nested_match(self, sample_opportunity):
        """Verifica que o Match está aninhado corretamente."""
        assert sample_opportunity.match.home_team == "Palmeiras"
        assert sample_opportunity.match.league == "Brasileirão"
