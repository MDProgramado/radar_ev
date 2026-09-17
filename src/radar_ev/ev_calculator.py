"""
ev_calculator.py — Cálculos matemáticos de valor esperado.

Funções puras para:
- Remover o overround (vig) das odds da casa antes da comparação com o modelo.
- Calcular o EV percentual entre a odd justa e a odd oferecida.
- Criar objetos Opportunity a partir dos componentes do pipeline.
"""

from typing import List, Optional, Sequence

from radar_ev.config import settings
from radar_ev.models import Match, Odds, Opportunity, Prediction


def calculate_ev(prediction: Prediction, offered_odd: float) -> float:
    """Calcula o valor esperado percentual.

    Fórmula:
        EV% = (probabilidade_real * odd_oferecida - 1) × 100

    Um EV positivo indica que a odd oferecida pela casa é alta o suficiente
    para compensar o risco real do evento (haver valor na aposta).

    Args:
        prediction: Predição do modelo com a probabilidade real.
        offered_odd: Odd decimal oferecida pela casa de apostas.

    Returns:
        EV percentual. Positivo = oportunidade, negativo = sem valor.

    Example:
        >>> pred = Prediction(match_id=1, market="corners", probability=0.50, fair_odd=2.00, model_version="v1")
        >>> calculate_ev(pred, 2.50)
        25.0  # Oportunidade com 25% de EV positivo.
    """
    if offered_odd <= 0:
        return -100.0  # Proteção contra erro matemático
    
    # Cálculo real do EV: (Probabilidade de vitória * Odd Oferecida) - 1
    ev_decimal = (prediction.probability * offered_odd) - 1.0
    return ev_decimal * 100.0


def remove_vig(odds: Sequence[float], method: str = "proportional") -> List[float]:
    """Remove o overround (vig) de um conjunto de odds mutuamente exaustivas.

    Método proporcional (default):
        A casa embute uma margem no preço, de modo que a soma das "probabilidades
        implícitas" (1/odd) excede 1.0. Remover o vig significa renormalizar:

            p_impl_i  = 1 / odd_i
            margem    = Σ_j (1 / odd_j)          # 1.0 + overround (ex: 1.05)
            p_fair_i  = p_impl_i / margem        # Σ p_fair = 1.0

        p_i_justa = (1/odd_i) / sum(1/odd_j)

    Aplica-se a mercados de 2 resultados (Over/Under de corners, cards e goals)
    e naturalmente a mercados com 3+ resultados mutuamente exaustivos (ex: 1X2),
    desde que as odds fornecidas fechem o conjunto de resultados.

    Args:
        odds: Odds decimais > 1.0 cobrindo todos os resultados do mercado,
              na mesma ordem dos participantes (ex: [1.90, 1.90] para Over/Under).
        method: Método de remoção. Atualmente apenas "proportional".

    Returns:
        Liste de probabilidades justas (soma = 1.0), na mesma ordem das odds
        de entrada.

    Raises:
        ValueError: Se `method` não for suportado, se houver menos de 2 odds
                    ou se alguma odd for <= 1.0.

    Example:
        >>> remove_vig([1.90, 1.90])     # margem de ~5.26% → 50%/50%
        [0.5, 0.5]
        >>> remove_vig([2.10, 1.75])     # somas das p_justas = 1.0
        [0.4545..., 0.5454...]
    """
    if method != "proportional":
        raise ValueError(f"Método de remoção de vig não suportado: {method!r}")
    if len(odds) < 2:
        raise ValueError("A remoção de vig exige ao menos 2 resultados")
    if any(o <= 1.0 for o in odds):
        raise ValueError("Odds devem ser > 1.0 para remoção de vig")

    implied = [1.0 / o for o in odds]
    margin = sum(implied)
    return [p / margin for p in implied]


def calculate_ev_vig_removed(
    prediction: Prediction,
    offered_odd: float,
    complementary_odd: float,
    method: str = settings.vig_removal_method,
) -> float:
    """Calcula o EV comparando o modelo com a odd da casa SEM a margem (vig).

    A odd bruta (ex: Over 9.5 = 1.90) já carrega o overround da Betano. Antes
    de comparar com a probabilidade do modelo, recalculamos a odd justa do
    mesmo evento usando também a odd do resultado complementar (Under 9.5):

        p_fair_over = (1/over) / ((1/over) + (1/under))
        odd_fair_over = 1 / p_fair_over
        EV% = (p_modelo × odd_fair_over − 1) × 100

    Como a remoção de vig só aumenta o preço (a margem encolhe a odd), este EV
    é sempre ≥ EV com a odd bruta — usar a odd bruta SUBESTIMA o valor real.

    Args:
        prediction: Predição do modelo (probabilidade real).
        offered_odd: Odd bruta do mercado avaliado (ex: Over 9.5).
        complementary_odd: Odd bruta do resultado complementar (Under 9.5).
        method: Método de remoção de vig (default vindo do settings).

    Returns:
        EV percentual calculado com a odd justa (sem margem da casa).
    """
    probabilities = remove_vig([offered_odd, complementary_odd], method)
    fair_offered_odd = 1.0 / probabilities[0]
    return calculate_ev(prediction, fair_offered_odd)


def calculate_kelly(
    probability: float,
    offered_odd: float,
    fraction: Optional[float] = None,
) -> float:
    """Calcula a porcentagem da banca a ser apostada usando o Critério de Kelly Fracionário.

    Fórmula: f* = (bp - q) / b
    Onde:
      b = odd decimal oferecida - 1 (lucro potencial)
      p = probabilidade estimada pelo modelo
      q = 1 - p (probabilidade de perda)

    Args:
        probability: Probabilidade de vitória (0.0 a 1.0).
        offered_odd: Odd decimal oferecida pela casa (ex: 2.10).
        fraction: Fator de mitigação de risco. Se None, usa `settings.kelly_fraction`
                  (default 0.25 = Quarter Kelly).

    Returns:
        Porcentagem da banca recomendada para a aposta (máximo de 5% por segurança).
    """
    if offered_odd <= 1.0 or probability <= 0.0:
        return 0.0

    if fraction is None:
        fraction = settings.kelly_fraction

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
    vig_removed: Optional[bool] = None,
) -> Opportunity:
    """Cria um objeto Opportunity a partir dos componentes do pipeline.

    Args:
        match: Partida analisada.
        prediction: Predição do modelo.
        odds: Odd oferecida pela casa.
        ev_percent: Valor esperado percentual calculado.
        reasoning: Justificativa da oportunidade.
        vig_removed: True se o EV usou odd justa (par Over/Under); False se
                     usou odd bruta (fallback). Persistido para monitoramento.

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
        vig_removed=vig_removed,
    )
