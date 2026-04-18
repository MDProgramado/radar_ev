from typing import Optional
from radar_ev.models import Prediction, Odds, Opportunity, Match

def calculate_ev(prediction: Prediction, offered_odd: float) -> float:
    """Retorna o valor esperado percentual: (fair_odd - offered_odd)/offered_odd * 100"""
    return (prediction.fair_odd - offered_odd) / offered_odd * 100

def create_opportunity(match: Match, prediction: Prediction, odds: Odds, ev_percent: float) -> Opportunity:
    return Opportunity(
        match=match,
        market=prediction.market,
        fair_odd=prediction.fair_odd,
        offered_odd=odds.odd_value,
        ev_percent=ev_percent,
        confidence=prediction.probability,
        reasoning="EV positivo"
    )
