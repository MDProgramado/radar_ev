from datetime import datetime, timezone, timedelta
from radar_ev.models import Match, Odds, Prediction

def get_mock_matches() -> list[Match]:
    """Retorna uma lista de partidas fictícias para teste."""
    now = datetime.now(timezone.utc)
    matches = [
        Match(
            id=1,
            home_team="Flamengo",
            away_team="Fluminense",
            datetime=now + timedelta(hours=2),
            league="Brasileirão",
            referee="Wilton Sampaio",
            home_lineup_confirmed=True,
            away_lineup_confirmed=True,
            motivation_index=8.5,
        ),
        Match(
            id=2,
            home_team="Palmeiras",
            away_team="Corinthians",
            datetime=now + timedelta(hours=3),
            league="Brasileirão",
            referee="Raphael Claus",
            home_lineup_confirmed=False,
            away_lineup_confirmed=True,
            motivation_index=6.0,
        ),
    ]
    return matches

def get_mock_odds(match_id: int) -> list[Odds]:
    """Retorna odds fictícias para um jogo."""
    return [
        Odds(
            match_id=match_id,
            market="corners_over_9.5",
            odd_value=1.85,
            offered_by="betano",
            timestamp=datetime.utcnow()
        ),
        Odds(
            match_id=match_id,
            market="cards_over_4.5",
            odd_value=2.10,
            offered_by="betano",
            timestamp=datetime.utcnow()
        )
    ]

def get_mock_prediction(match_id: int, market: str) -> Prediction:
    """Retorna predição fictícia para um mercado."""
    prob = 0.55 if market == "corners_over_9.5" else 0.60
    return Prediction(
        match_id=match_id,
        market=market,
        probability=prob,
        fair_odd=1/prob,
        model_version="mock_v1"
    )