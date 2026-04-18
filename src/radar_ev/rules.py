from typing import Tuple
from radar_ev.models import Match, Opportunity

# RN01: Índice de motivação (exemplo: times lutando contra rebaixamento, título, etc.)
def motivation_filter(opp: Opportunity, min_motivation: float = 7.0) -> Tuple[bool, str]:
    """Só aceita oportunidades se a motivação do jogo for alta."""
    if opp.match.motivation_index < min_motivation:
        return False, f"Motivação baixa: {opp.match.motivation_index} < {min_motivation}"
    return True, "Motivação OK"

# RN02: Anti-Reserva – apenas se as escalações estiverem confirmadas para times favoritos
def lineup_filter(opp: Opportunity) -> Tuple[bool, str]:
    """Para mercados de gols/vitórias, exige escalação confirmada de ambos os times."""
    if opp.market in ["goals", "wins"]:
        if not (opp.match.home_lineup_confirmed and opp.match.away_lineup_confirmed):
            return False, "Escalação não confirmada"
    return True, "Escalação OK"

# RN03: Derby Mode – em clássicos, desativa mercados de gols/vitórias e força cartões
def derby_mode_filter(opp: Opportunity, derby_teams: list) -> Tuple[bool, str]:
    is_derby = (opp.match.home_team in derby_teams and opp.match.away_team in derby_teams)
    if is_derby:
        if opp.market in ["goals", "wins"]:
            return False, "Derby mode: mercado de gols/vitórias desativado"
        if opp.market == "cards":
            # podemos ajustar a confiança depois, mas por enquanto apenas permite
            return True, "Derby mode ativado para cartões"
    return True, "OK"

# RN04: Bet Builders – será implementada depois, pois requer correlação estatística
# Por ora, apenas um placeholder.
def bet_builder_filter(opp: Opportunity) -> Tuple[bool, str]:
    # TODO: implementar lógica de odds combinadas
    return True, "Bet Builder não implementado ainda"

# RN05: Janela de operação – verificar se o jogo ainda não começou
def pre_match_filter(opp: Opportunity) -> Tuple[bool, str]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if opp.match.datetime <= now:
        return False, f"Jogo já começou ou está em andamento: {opp.match.datetime}"
    return True, "Pré-jogo OK"

# Aplicar todas as regras em cadeia
def apply_all_rules(opp: Opportunity, derby_teams: list, min_motivation: float = 7.0) -> Tuple[bool, str]:
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
    return True, "Todas as regras aprovadas"