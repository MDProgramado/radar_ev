"""
rules.py — Motor de regras de negócio do Radar +EV.

Implementa as cinco regras de negócio (RN01–RN05) como funções puras que retornam
`(aprovado: bool, motivo: str)`. O Strategy Pattern permite encadear regras
e parar no primeiro filtro que rejeitar a oportunidade.

Regras:
- RN01: Índice de Motivação — jogos decisivos têm mais escanteios/cartões.
- RN02: Anti-Reserva — times com escalação mista não devem ser apostados.
- RN03: Derby Mode — clássicos desativam mercados de gols/vitórias.
- RN04: Bet Builders — placeholder para odds combinadas (futuro).
- RN05: Janela de Operação — apenas jogos pré-match são analisados.
"""

from datetime import datetime, timezone
from typing import List, Tuple

from radar_ev.models import Opportunity


# =============================================================================
# RN01 — Índice de Motivação
# =============================================================================
def motivation_filter(
    opp: Opportunity, min_motivation: float = 7.0
) -> Tuple[bool, str]:
    """Só aceita oportunidades se a motivação do jogo for alta.

    Jogos decisivos (fuga de rebaixamento, título, clássico) tendem a ter
    mais escanteios, cartões e finalizações.

    Args:
        opp: Oportunidade a ser avaliada.
        min_motivation: Índice mínimo de motivação (0.0 a 10.0).

    Returns:
        Tupla (aprovado, motivo).
    """
    if opp.match.motivation_index < min_motivation:
        return False, (
            f"RN01 — Motivação baixa: {opp.match.motivation_index:.1f} < {min_motivation:.1f} "
            f"({opp.match.home_team} vs {opp.match.away_team})"
        )
    return True, "RN01 — Motivação OK"


# =============================================================================
# RN02 — Anti-Reserva
# =============================================================================
def lineup_filter(opp: Opportunity) -> Tuple[bool, str]:
    """Para mercados de gols/vitórias, exige escalação confirmada de ambos os times.

    Times que poupam titulares (escalação mista) têm desempenho estatístico
    inferior — não apostar neles para mercados sensíveis a qualidade do elenco.

    Nota: mercados de escanteios e cartões não são afetados por esta regra,
    pois são mais estáveis independentemente da escalação.
    """
    sensitive_markets = {"goals", "wins", "match_winner", "goals_over", "goals_under"}

    if opp.market.lower() in sensitive_markets or opp.market.lower().startswith("goals"):
        if not (opp.match.home_lineup_confirmed and opp.match.away_lineup_confirmed):
            return False, (
                f"RN02 — Escalação não confirmada para mercado sensível: {opp.market} "
                f"({opp.match.home_team} vs {opp.match.away_team})"
            )
    return True, "RN02 — Escalação OK"


# =============================================================================
# RN03 — Derby Mode
# =============================================================================
def derby_mode_filter(
    opp: Opportunity, derby_teams: List[str]
) -> Tuple[bool, str]:
    """Em clássicos, desativa mercados de gols/vitórias e foca em cartões.

    Se ambos os times estão na lista de derby_teams, o comportamento
    da partida é atípico: mais faltas, mais cartões, menos gols.

    Args:
        opp: Oportunidade a ser avaliada.
        derby_teams: Lista de times considerados "derby".
    """
    is_derby = (
        opp.match.home_team in derby_teams
        and opp.match.away_team in derby_teams
    )

    if is_derby:
        blocked_markets = {"goals", "wins", "match_winner", "goals_over", "goals_under"}
        if opp.market.lower() in blocked_markets or opp.market.lower().startswith("goals"):
            return False, (
                f"RN03 — Derby mode: mercado '{opp.market}' desativado em "
                f"{opp.match.home_team} vs {opp.match.away_team}"
            )
        if "card" in opp.market.lower():
            return True, "RN03 — Derby mode ativado: cartões permitidos"

    return True, "RN03 — OK (não é derby ou mercado permitido)"


# =============================================================================
# RN04 — Bet Builders (placeholder)
# =============================================================================
def bet_builder_filter(opp: Opportunity) -> Tuple[bool, str]:
    """Placeholder para validação de Bet Builders.

    Futuro: calcular a odd combinada teórica (produto das probabilidades)
    e comparar com a odd oferecida para múltiplos eventos no mesmo time.

    Por enquanto, sempre aprova.
    """
    # TODO: Implementar lógica de odds combinadas quando disponível
    return True, "RN04 — Bet Builder: não implementado (aprovado por padrão)"


# =============================================================================
# RN05 — Janela de Operação
# =============================================================================
def pre_match_filter(opp: Opportunity) -> Tuple[bool, str]:
    """Verifica se o jogo ainda não começou (apenas pré-jogo).

    Apenas jogos futuros são válidos para análise. Jogos em andamento
    ou já finalizados são rejeitados.
    """
    now = datetime.now(timezone.utc)
    if opp.match.datetime <= now:
        return False, (
            f"RN05 — Jogo já começou ou está em andamento: "
            f"{opp.match.home_team} vs {opp.match.away_team} "
            f"(início: {opp.match.datetime.isoformat()})"
        )
    return True, "RN05 — Pré-jogo OK"


# =============================================================================
# Encadeamento de Regras
# =============================================================================
def apply_all_rules(
    opp: Opportunity,
    derby_teams: List[str],
    min_motivation: float = 7.0,
) -> Tuple[bool, str]:
    """Aplica todas as regras de negócio em cadeia (Strategy Pattern).

    Para no primeiro filtro que rejeitar a oportunidade, economizando
    processamento desnecessário.

    Args:
        opp: Oportunidade a ser avaliada.
        derby_teams: Lista de times para Derby Mode.
        min_motivation: Índice mínimo de motivação.

    Returns:
        Tupla (aprovado, motivo). Se aprovado=False, motivo contém o filtro que rejeitou.
    """
    rules = [
        lambda o: motivation_filter(o, min_motivation),
        lineup_filter,
        lambda o: derby_mode_filter(o, derby_teams),
        bet_builder_filter,
        pre_match_filter,
    ]

    for rule in rules:
        ok, reason = rule(opp)
        if not ok:
            return False, reason

    return True, "Todas as regras aprovadas ✅"