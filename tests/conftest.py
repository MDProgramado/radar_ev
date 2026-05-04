"""
conftest.py — Fixtures compartilhadas para testes do Radar +EV.

Fornece objetos de teste reutilizáveis (Match, Odds, Prediction, Opportunity)
para evitar duplicação entre módulos de teste.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

from radar_ev.models import Match, Odds, Opportunity, Prediction

# Garante que variáveis de ambiente necessárias existam para testes
os.environ.setdefault("RAPIDAPI_KEY", "test_key_12345")
os.environ.setdefault("RAPIDAPI_HOST", "v3.football.api-sports.io")
os.environ.setdefault("ODDS_API_KEY", "test_odds_key")
os.environ.setdefault("TELEGRAM_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456789")
os.environ.setdefault("MIN_EV_PERCENT", "5.0")
os.environ.setdefault("PRE_MATCH_HOURS", "4")
os.environ.setdefault("DERBY_TEAMS", "Flamengo,Fluminense,Boca Juniors,River Plate")


@pytest.fixture
def future_datetime():
    """Retorna um datetime 2 horas no futuro (UTC)."""
    return datetime.now(timezone.utc) + timedelta(hours=2)


@pytest.fixture
def past_datetime():
    """Retorna um datetime 1 hora no passado (UTC)."""
    return datetime.now(timezone.utc) - timedelta(hours=1)


@pytest.fixture
def sample_match(future_datetime):
    """Match de exemplo para testes."""
    return Match(
        id=1001,
        home_team="Palmeiras",
        away_team="Corinthians",
        datetime=future_datetime,
        league="Brasileirão",
        referee="Raphael Claus",
        home_lineup_confirmed=True,
        away_lineup_confirmed=True,
        motivation_index=8.5,
        home_team_id=121,
        away_team_id=131,
        league_id=71,
        season=2025,
    )


@pytest.fixture
def derby_match(future_datetime):
    """Match de derby para testes (Flamengo vs Fluminense)."""
    return Match(
        id=1002,
        home_team="Flamengo",
        away_team="Fluminense",
        datetime=future_datetime,
        league="Brasileirão",
        referee="Wilton Sampaio",
        home_lineup_confirmed=True,
        away_lineup_confirmed=True,
        motivation_index=9.0,
        home_team_id=127,
        away_team_id=124,
        league_id=71,
        season=2025,
    )


@pytest.fixture
def sample_odds():
    """Odds de exemplo para testes."""
    return Odds(
        match_id=1001,
        market="corners_over_9.5",
        odd_value=1.85,
        offered_by="betano",
    )


@pytest.fixture
def sample_prediction():
    """Prediction de exemplo para testes."""
    return Prediction(
        match_id=1001,
        market="corners_over_9.5",
        probability=0.58,
        fair_odd=1.72,
        model_version="poisson_v1",
    )


@pytest.fixture
def positive_ev_prediction():
    """Prediction com EV positivo (fair_odd > offered_odd típica)."""
    return Prediction(
        match_id=1001,
        market="corners_over_9.5",
        probability=0.48,
        fair_odd=2.08,
        model_version="poisson_v1",
    )


@pytest.fixture
def sample_opportunity(sample_match):
    """Opportunity de exemplo para testes."""
    return Opportunity(
        match=sample_match,
        market="corners_over_9.5",
        fair_odd=2.10,
        offered_odd=1.85,
        ev_percent=13.5,
        confidence=0.72,
        reasoning="EV positivo identificado pelo modelo Poisson",
    )


@pytest.fixture
def derby_teams():
    """Lista de times para Derby Mode."""
    return ["Flamengo", "Fluminense", "Boca Juniors", "River Plate"]
