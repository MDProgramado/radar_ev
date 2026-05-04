"""
ev_calculator.py — Cálculos matemáticos de valor esperado.

Funções puras para:
- Calcular o EV percentual entre a odd justa e a odd oferecida.
- Criar objetos Opportunity a partir dos componentes do pipeline.
"""

from radar_ev.models import Match, Odds, Opportunity, Prediction


def calculate_ev(prediction: Prediction, offered_odd: float) -> float:
    """Calcula o valor esperado percentual.

    Fórmula:
        EV% = (fair_odd - offered_odd) / offered_odd × 100

    Um EV positivo indica que a odd oferecida pela casa está acima do que
    o modelo estima como justo — ou seja, há "valor" na aposta.

    Args:
        prediction: Predição do modelo com a odd justa (fair_odd).
        offered_odd: Odd decimal oferecida pela casa de apostas.

    Returns:
        EV percentual. Positivo = oportunidade, negativo = sem valor.

    Example:
        >>> pred = Prediction(match_id=1, market="corners", probability=0.55, fair_odd=1.82, model_version="v1")
        >>> calculate_ev(pred, 1.85)
        -1.62...  # Odd oferecida próxima da justa, sem valor significativo.
    """
    if offered_odd <= 0:
        return -100.0  # Proteção contra divisão por zero
    return (prediction.fair_odd - offered_odd) / offered_odd * 100


def calculate_kelly(probability: float, offered_odd: float, fraction: float = 0.25) -> float:
    """Calcula a porcentagem da banca a ser apostada usando o Critério de Kelly Fracionário.

    Fórmula: f* = (bp - q) / b
    Onde:
      b = odd decimal oferecida - 1 (lucro potencial)
      p = probabilidade estimada pelo modelo
      q = 1 - p (probabilidade de perda)

    Args:
        probability: Probabilidade de vitória (0.0 a 1.0).
        offered_odd: Odd decimal oferecida pela casa (ex: 2.10).
        fraction: Fator de mitigação de risco (ex: 0.25 = Quarter Kelly).

    Returns:
        Porcentagem da banca recomendada para a aposta (máximo de 5% por segurança).
    """
    if offered_odd <= 1.0 or probability <= 0.0:
        return 0.0

    b = offered_odd - 1.0
    p = probability
    q = 1.0 - p

    kelly_fraction = (b * p - q) / b

    if kelly_fraction <= 0:
        return 0.0

    safe_fraction = kelly_fraction * fraction
    
    # Limita a sugestão a no máximo 5% da banca (Gestão de Risco Estrita)
    return min(safe_fraction * 100.0, 5.0)


def create_opportunity(
    match: Match,
    prediction: Prediction,
    odds: Odds,
    ev_percent: float,
    reasoning: str = "EV positivo identificado pelo modelo Poisson",
) -> Opportunity:
    """Cria um objeto Opportunity a partir dos componentes do pipeline.

    Args:
        match: Partida analisada.
        prediction: Predição do modelo.
        odds: Odd oferecida pela casa.
        ev_percent: Valor esperado percentual calculado.
        reasoning: Justificativa da oportunidade.

    Returns:
        Objeto Opportunity pronto para filtragem por regras e envio.
    """
    stake = calculate_kelly(prediction.probability, odds.odd_value)

    return Opportunity(
        match=match,
        market=prediction.market,
        fair_odd=prediction.fair_odd,
        offered_odd=odds.odd_value,
        ev_percent=round(ev_percent, 2),
        confidence=prediction.probability,
        recommended_stake_percent=round(stake, 2),
        reasoning=reasoning,
    )
