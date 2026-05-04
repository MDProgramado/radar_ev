"""
predictor.py — Modelo de Machine Learning baseado em distribuição de Poisson.

Implementa predições para mercados estatísticos de futebol:
- Escanteios (corners): Over/Under com base na média histórica dos times.
- Cartões (cards): Over/Under com base na média de faltas e cartões.
- Finalizações (shots): Placeholder (futuro).

Premissa: O número total de escanteios (ou cartões) em uma partida segue uma
distribuição de Poisson. Isso é razoável para eventos raros e independentes.

Estimativa da média (λ):
    λ_total = média_a_favor_mandante + média_contra_visitante

Cálculo:
    P(total > k) = 1 – Σ_{i=0}^{k} (e^{-λ} × λ^i / i!)

Limitações:
    - Não considera correlação entre escanteios e cartões.
    - Não usa xG, posse de bola, etc.
    - Estimativa de λ é linear (soma de médias), não usa regressão.
"""

from typing import Optional

import structlog
from scipy.stats import poisson

from radar_ev.models import Prediction

logger = structlog.get_logger(__name__)

# Valores default para quando a API não retorna dados históricos
DEFAULT_CORNERS_AVG = 5.0  # Média mundial aproximada de escanteios por time
DEFAULT_CARDS_AVG = 2.0    # Média mundial aproximada de cartões por time
DEFAULT_SHOTS_AVG = 12.0   # Média mundial aproximada de finalizações por time


# =============================================================================
# Funções de Distribuição de Poisson
# =============================================================================

def poisson_prob_over_k(lambda_mean: float, k: int) -> float:
    """Calcula P(X > k) para uma distribuição de Poisson com média λ.

    Args:
        lambda_mean: Parâmetro λ (média esperada de eventos).
        k: Limiar do mercado (ex: 9 para Over 9.5).

    Returns:
        Probabilidade de o total ser estritamente maior que k.

    Example:
        >>> poisson_prob_over_k(10.5, 9)  # P(corners > 9) quando λ=10.5
        0.6453...
    """
    if lambda_mean <= 0:
        return 0.0

    # P(X <= k) = Σ P(X = i) para i de 0 até k
    prob_le_k = sum(poisson.pmf(i, lambda_mean) for i in range(k + 1))
    return 1.0 - prob_le_k


def poisson_prob_under_k(lambda_mean: float, k: int) -> float:
    """Calcula P(X < k) para uma distribuição de Poisson com média λ.

    Args:
        lambda_mean: Parâmetro λ (média esperada de eventos).
        k: Limiar do mercado (ex: 10 para Under 9.5, ou seja, ≤ 9).

    Returns:
        Probabilidade de o total ser estritamente menor que k.
    """
    if lambda_mean <= 0:
        return 1.0

    return sum(poisson.pmf(i, lambda_mean) for i in range(k))


def poisson_prob_exact(lambda_mean: float, k: int) -> float:
    """Calcula P(X = k) para uma distribuição de Poisson com média λ."""
    if lambda_mean <= 0:
        return 0.0
    return float(poisson.pmf(k, lambda_mean))


# =============================================================================
# Estimativas de Lambda (λ)
# =============================================================================

def estimate_total_corners(
    home_avg_for: float,
    away_avg_against: float,
    away_avg_for: float = 0.0,
    home_avg_against: float = 0.0,
) -> float:
    """Estima o λ total de escanteios usando um modelo baseado em Forças (Strengths).

    Evolução do modelo: Em vez de média simples, calcula a 'Força de Ataque'
    (capacidade de gerar escanteios) e a 'Força de Defesa' (tendência a ceder escanteios)
    relativas à média global (baseline), aplicando Vantagem do Mandante (Home Advantage).

    Args:
        home_avg_for: Média de escanteios a favor do mandante (em casa).
        away_avg_against: Média de escanteios sofridos pelo visitante (fora).
        away_avg_for: Média de escanteios a favor do visitante (fora).
        home_avg_against: Média de escanteios sofridos pelo mandante (em casa).

    Returns:
        Estimativa do λ total de escanteios na partida.
    """
    baseline = 5.0  # Média global conservadora por time

    if away_avg_for > 0 and home_avg_against > 0:
        # Calcula Forças Relativas
        home_attack_strength = home_avg_for / baseline
        home_defense_strength = home_avg_against / baseline
        
        away_attack_strength = away_avg_for / baseline
        away_defense_strength = away_avg_against / baseline
        
        # Escanteios Esperados = Ataque * Defesa do Adversário * Baseline
        exp_home_corners = home_attack_strength * away_defense_strength * baseline
        exp_away_corners = away_attack_strength * home_defense_strength * baseline
        
        # Vantagem do Mandante (Home Advantage) ~10%
        exp_home_corners *= 1.10
        exp_away_corners *= 0.90
        
        return exp_home_corners + exp_away_corners
    else:
        # Modelo simplificado ajustado para o viés de casa
        return (home_avg_for + away_avg_against) * 1.05


def estimate_total_cards(
    home_avg_cards: float,
    away_avg_cards: float,
    referee_avg_cards: float = 0.0,
) -> float:
    """Estima o λ total de cartões para a partida.

    Considera as médias de cartões dos dois times e, opcionalmente,
    a média de cartões do árbitro como fator de ajuste.

    Args:
        home_avg_cards: Média de cartões do mandante.
        away_avg_cards: Média de cartões do visitante.
        referee_avg_cards: Média de cartões por jogo do árbitro.

    Returns:
        Estimativa do λ total de cartões na partida.
    """
    base = home_avg_cards + away_avg_cards

    if referee_avg_cards > 0:
        # Ajuste pelo perfil do árbitro (peso de 30%)
        avg_referee_contribution = referee_avg_cards / 2  # dividido por 2 times
        adjusted = base * 0.7 + (avg_referee_contribution * 2) * 0.3
        return adjusted

    return base


# =============================================================================
# Extração de Estatísticas da API-Football
# =============================================================================

def extract_corners_stats(stats_data: dict) -> dict:
    """Extrai médias de escanteios da resposta da API-Football (/teams/statistics).

    A estrutura esperada da API é:
        response.corners.for.average.total
        response.corners.against.average.total

    Args:
        stats_data: Resposta completa da API-Football para /teams/statistics.

    Returns:
        Dicionário com chaves 'avg_for' e 'avg_against'.
    """
    try:
        response = stats_data.get("response", stats_data)

        # Navega na estrutura da API-Football
        corners = response.get("corners", {})
        avg_for = float(
            corners.get("for", {}).get("average", {}).get("total", DEFAULT_CORNERS_AVG)
            or DEFAULT_CORNERS_AVG
        )
        avg_against = float(
            corners.get("against", {}).get("average", {}).get("total", DEFAULT_CORNERS_AVG)
            or DEFAULT_CORNERS_AVG
        )

        return {"avg_for": avg_for, "avg_against": avg_against}

    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "corners_stats_extraction_failed",
            error=str(exc),
            fallback=DEFAULT_CORNERS_AVG,
        )
        return {"avg_for": DEFAULT_CORNERS_AVG, "avg_against": DEFAULT_CORNERS_AVG}


def extract_cards_stats(stats_data: dict) -> dict:
    """Extrai médias de cartões da resposta da API-Football.

    A estrutura esperada da API é:
        response.cards (com intervalos de tempo e contagens)

    Como a API retorna cartões por intervalo de tempo, somamos todos
    e dividimos pelo total de jogos.

    Args:
        stats_data: Resposta completa da API-Football.

    Returns:
        Dicionário com chave 'avg_total'.
    """
    try:
        response = stats_data.get("response", stats_data)
        cards = response.get("cards", {})
        fixtures_played = int(
            response.get("fixtures", {}).get("played", {}).get("total", 1) or 1
        )

        # Soma cartões de todos os intervalos de tempo
        total_yellow = 0
        total_red = 0

        for interval_data in cards.get("yellow", {}).values():
            if isinstance(interval_data, dict):
                total_yellow += int(interval_data.get("total", 0) or 0)

        for interval_data in cards.get("red", {}).values():
            if isinstance(interval_data, dict):
                total_red += int(interval_data.get("total", 0) or 0)

        total_cards = total_yellow + total_red
        avg_total = total_cards / max(fixtures_played, 1)

        return {"avg_total": avg_total}

    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "cards_stats_extraction_failed",
            error=str(exc),
            fallback=DEFAULT_CARDS_AVG,
        )
        return {"avg_total": DEFAULT_CARDS_AVG}


# =============================================================================
# Predições Principais
# =============================================================================

async def make_corners_prediction(
    match_id: int,
    home_team_id: int,
    away_team_id: int,
    league_id: int,
    season: int,
    football_api: object,
    threshold: float = 9.5,
) -> Prediction:
    """Gera predição de escanteios usando modelo Poisson.

    Busca estatísticas históricas dos dois times via API-Football e calcula
    a probabilidade de o total de escanteios superar o limiar (ex: 9.5).

    Args:
        match_id: ID da partida.
        home_team_id: ID do time mandante.
        away_team_id: ID do time visitante.
        league_id: ID da liga.
        season: Temporada (ex: 2025).
        football_api: Instância do FootballAPICollector.
        threshold: Limiar do mercado (ex: 9.5 → Over 9.5).

    Returns:
        Prediction com probabilidade, odd justa e versão do modelo.
    """
    log = logger.bind(
        match_id=match_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        market=f"corners_over_{threshold}",
    )

    try:
        # Busca estatísticas dos dois times
        home_stats = await football_api.get_team_statistics(home_team_id, league_id, season)
        away_stats = await football_api.get_team_statistics(away_team_id, league_id, season)

        # Extrai médias de escanteios
        home_corners = extract_corners_stats(home_stats)
        away_corners = extract_corners_stats(away_stats)

        # Calcula λ total
        lambda_total = estimate_total_corners(
            home_avg_for=home_corners["avg_for"],
            away_avg_against=away_corners["avg_against"],
            away_avg_for=away_corners["avg_for"],
            home_avg_against=home_corners["avg_against"],
        )

        log.info(
            "poisson_lambda_calculated",
            lambda_total=round(lambda_total, 2),
            home_for=home_corners["avg_for"],
            away_against=away_corners["avg_against"],
        )

        # Calcula probabilidade de Over (threshold)
        k = int(threshold)  # 9.5 → k=9, P(X > 9) = P(X >= 10)
        prob = poisson_prob_over_k(lambda_total, k)

        # Garante que a probabilidade está em um range razoável
        prob = max(0.01, min(0.99, prob))

        # Odd justa
        fair_odd = 1.0 / prob

        log.info(
            "corners_prediction_made",
            probability=round(prob, 4),
            fair_odd=round(fair_odd, 2),
        )

        market_name = f"corners_over_{threshold}"
        return Prediction(
            match_id=match_id,
            market=market_name,
            probability=round(prob, 4),
            fair_odd=round(fair_odd, 2),
            model_version="poisson_v1",
        )

    except Exception as exc:
        log.error("corners_prediction_failed", error=str(exc))
        # Fallback com probabilidade neutra
        return Prediction(
            match_id=match_id,
            market=f"corners_over_{threshold}",
            probability=0.50,
            fair_odd=2.0,
            model_version="poisson_v1_fallback",
        )


async def make_cards_prediction(
    match_id: int,
    home_team_id: int,
    away_team_id: int,
    league_id: int,
    season: int,
    football_api: object,
    threshold: float = 4.5,
) -> Prediction:
    """Gera predição de cartões usando modelo Poisson.

    Similar à predição de escanteios, mas usa estatísticas de cartões.

    Args:
        match_id: ID da partida.
        home_team_id: ID do time mandante.
        away_team_id: ID do time visitante.
        league_id: ID da liga.
        season: Temporada.
        football_api: Instância do FootballAPICollector.
        threshold: Limiar do mercado (ex: 4.5 → Over 4.5).

    Returns:
        Prediction com probabilidade e odd justa.
    """
    log = logger.bind(match_id=match_id, market=f"cards_over_{threshold}")

    try:
        home_stats = await football_api.get_team_statistics(home_team_id, league_id, season)
        away_stats = await football_api.get_team_statistics(away_team_id, league_id, season)

        home_cards = extract_cards_stats(home_stats)
        away_cards = extract_cards_stats(away_stats)

        lambda_total = estimate_total_cards(
            home_avg_cards=home_cards["avg_total"],
            away_avg_cards=away_cards["avg_total"],
        )

        log.info(
            "cards_lambda_calculated",
            lambda_total=round(lambda_total, 2),
        )

        k = int(threshold)
        prob = poisson_prob_over_k(lambda_total, k)
        prob = max(0.01, min(0.99, prob))
        fair_odd = 1.0 / prob

        log.info(
            "cards_prediction_made",
            probability=round(prob, 4),
            fair_odd=round(fair_odd, 2),
        )

        return Prediction(
            match_id=match_id,
            market=f"cards_over_{threshold}",
            probability=round(prob, 4),
            fair_odd=round(fair_odd, 2),
            model_version="poisson_cards_v1",
        )

    except Exception as exc:
        log.error("cards_prediction_failed", error=str(exc))
        return Prediction(
            match_id=match_id,
            market=f"cards_over_{threshold}",
            probability=0.50,
            fair_odd=2.0,
            model_version="poisson_cards_v1_fallback",
        )


def make_prediction_from_lambda(
    match_id: int,
    market: str,
    lambda_total: float,
    threshold: float,
    model_version: str = "poisson_manual_v1",
) -> Prediction:
    """Gera predição a partir de um λ já calculado (útil para testes).

    Args:
        match_id: ID da partida.
        market: Nome do mercado (ex: "corners_over_9.5").
        lambda_total: Parâmetro λ da Poisson.
        threshold: Limiar do mercado.
        model_version: Versão do modelo.

    Returns:
        Prediction com probabilidade e odd justa.
    """
    k = int(threshold)
    prob = poisson_prob_over_k(lambda_total, k)
    prob = max(0.01, min(0.99, prob))
    fair_odd = 1.0 / prob

    return Prediction(
        match_id=match_id,
        market=market,
        probability=round(prob, 4),
        fair_odd=round(fair_odd, 2),
        model_version=model_version,
    )
