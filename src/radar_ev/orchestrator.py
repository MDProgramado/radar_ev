"""
orchestrator.py — Pipeline principal do Radar +EV.

Controla o fluxo completo de execução:
1. Carrega configurações.
2. Decide entre modo mock e modo real (API).
3. Busca partidas do dia, filtra por ligas de interesse.
4. Para cada partida: busca odds → gera predição Poisson → calcula EV.
5. Aplica regras de negócio (RN01–RN05).
6. Envia oportunidades aprovadas via Telegram.
7. Trata exceções e loga resultados.

Padrões aplicados:
- Clean Architecture: domínio separado da infraestrutura.
- Dependency Injection: componentes recebidos por parâmetro.
- Strategy Pattern: regras encadeadas.
"""

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import structlog

from radar_ev.alert.telegram import TelegramSender
from radar_ev.config import settings
from radar_ev.db import get_db_connection
from radar_ev.ev_calculator import (
    calculate_ev,
    calculate_ev_vig_removed,
    create_opportunity,
)
from radar_ev.vig_stats import vig_stats
from radar_ev.http_client import (
    ApiError,
    ApiPlanInsufficientError,
    ApiQuotaExhaustedError,
    RateLimitError,
    get_api_request_count,
    reset_api_request_count,
)
from radar_ev.logger import setup_logging
from radar_ev.models import Match, Opportunity
from radar_ev.predictor import (
    make_cards_prediction,
    make_corners_prediction,
    make_goals_prediction,
)
from radar_ev.rules import apply_all_rules

logger = structlog.get_logger(__name__)

# Flag em memória (não persistida): evita repetir o INFO de "resolução
# desabilitada" a cada ciclo do daemon em modo mock (Risco 5).
_logged_mock_resolution_disabled = False

# Flag em memória: True se o pipeline quebrou nesta execução (para o exit code
# do processo ir para 1 em vez de 0 — erros reais não podem parecer "verdes").
_pipeline_crashed = False

# =============================================================================
# Ligas de interesse — a Betano normalmente cobre estas ligas
# =============================================================================
LIGAS_INTERESSE = [
    # Brasil
    "Serie A",              # Brasileirão Série A
    "Serie B",              # Brasileirão Série B
    "Copa Do Brasil",
    "Paulista - A1",
    "Carioca - 1",
    "Mineiro - 1",
    "Gaúcho - 1",
    # Europa — Top 5
    "Premier League",
    "La Liga",
    "Serie A",              # Itália
    "Bundesliga",
    "Ligue 1",
    # Europa — Segundas divisões e outros
    "Championship",
    "Primeira Liga",        # Portugal
    "Eredivisie",           # Holanda
    "Süper Lig",            # Turquia
    "Pro League",           # Bélgica
    # Américas
    "MLS",
    "Liga MX",
    "Liga Profesional Argentina",
    "Primera Division",
    # Competições internacionais
    "UEFA Champions League",
    "UEFA Europa League",
    "CONMEBOL Libertadores",
    "CONMEBOL Sudamericana",
    "Copa Libertadores",
    "Copa Sudamericana",
]

# Mercados que o predictor Poisson suporta atualmente
SUPPORTED_MARKETS = {
    "corners": ["Mais Escanteios (Partida Completa)", "Menos Escanteios (Partida Completa)"],
    "cards": ["Mais Cartões (Partida Completa)", "Menos Cartões (Partida Completa)"],
    "goals": ["Mais Gols (Partida Completa)", "Menos Gols (Partida Completa)"],
}

def format_market_name(market: str) -> str:
    """Transforma o nome técnico do mercado em algo amigável para o usuário."""
    import re
    market_lower = market.lower()
    
    # Extrai o número (ex: 9.5)
    match = re.search(r"(\d+\.?\d*)", market_lower)
    threshold = match.group(1) if match else "X"
    
    if "corner" in market_lower:
        if "under" in market_lower and "over" not in market_lower.split("under")[-1]: 
            return f"Menos de {threshold} Escanteios (Partida Completa)"
        return f"Mais de {threshold} Escanteios (Partida Completa)"
        
    elif "card" in market_lower:
        if "under" in market_lower:
            return f"Menos de {threshold} Cartões (Partida Completa)"
        return f"Mais de {threshold} Cartões (Partida Completa)"
        
    elif "goal" in market_lower:
        if "under" in market_lower:
            return f"Menos de {threshold} Gols (Partida Completa)"
        return f"Mais de {threshold} Gols (Partida Completa)"
        
    return market.replace("_", " ").title()

# =============================================================================
# Rate Limit Control
# =============================================================================
RATE_LIMIT_DELAY = 0.6  # Segundos entre cada chamada (≈10 req/min)
MAX_RATE_LIMIT_RETRIES = 3


# =============================================================================
# Whitelist de Mercados (estrita)
# =============================================================================
# Única forma aceita: <tipo>_<over|under>_<linha decimal> (gerada pelo normalizador).
MARKET_PATTERN = re.compile(r"^(corners|cards|goals)_(over|under)_(\d+\.\d{1,2})$")

MARKET_THRESHOLD_RANGES = {
    "corners": (settings.market_corners_min, settings.market_corners_max),
    "cards": (settings.market_cards_min, settings.market_cards_max),
    "goals": (settings.market_goals_min, settings.market_goals_max),
}


def _find_pinnacle_reference(market: str, pinnacle_odds: Optional[list] = None) -> Optional[float]:
    """Retorna a odd da Pinnacle para o mesmo mercado/linha, se existir.

    A Pinnacle (margem ~2%) é a referência de preço justo do mercado; a odd
    capturada na Betano comparada à da Pinnacle revela value betting sem
    precisar das estatísticas do modelo.

    Args:
        market: Nome canônico do mercado (ex: "goals_over_2.5").
        pinnacle_odds: Lista de Odds da Pinnacle; None/vazia → sem referência.

    Returns:
        Odd da Pinnacle para o mercado, ou None.
    """
    for odds in pinnacle_odds or []:
        if odds.market == market:
            return float(odds.odd_value)
    return None


def _complementary_odd(market: str, odds_list: list) -> Optional[float]:
    """Retorna a odd bruta do resultado complementar (Under↔Over, mesma linha).

    Ex: "corners_over_9.5" → procura "corners_under_9.5" na lista de odds.
    Usada para remover o vig (margem da casa) antes de calcular o EV.

    Returns:
        Odd do resultado complementar, ou None se não encontrada /
        se o mercado não estiver no formato Over/Under da whitelist.
    """
    match = MARKET_PATTERN.match(market)
    if not match:
        return None
    market_type, direction, line = match.groups()
    opposite = "under" if direction == "over" else "over"
    complement_market = f"{market_type}_{opposite}_{line}"
    for odds in odds_list:
        if odds.market == complement_market:
            return odds.odd_value
    return None


def _find_divergent_line(market: str, odds_list: list) -> bool:
    """Detecta se existe um resultado complementar com limiar DIVERGENTE.

    Ex: avaliando "corners_over_9.5", existe "corners_under_10.5" na lista
    (mesmo tipo, direção oposta, mas linha diferente). A casa publicou o par
    em outra linha — sem par exato, mas candidato a interpolação futura.

    Returns:
        True se algum complemento de limiar diferente existe; False caso contrário.
    """
    match = MARKET_PATTERN.match(market)
    if not match:
        return False
    market_type, direction, line = match.groups()
    opposite = "under" if direction == "over" else "over"
    for odds in odds_list:
        other = MARKET_PATTERN.match(odds.market)
        if (
            other
            and other.group(1) == market_type
            and other.group(2) == opposite
            and other.group(3) != line
        ):
            return True
    return False


def _calculate_ev_without_vig(
    prediction: object,
    offered_odd: float,
    market: str,
    odds_list: list,
    strict: bool = False,
) -> Optional[tuple[float, bool, bool]]:
    """Calcula o EV usando a odd justa (sem vig) quando o par Over/Under existe.

    Se o mercado complementar não estiver na lista (ex: mock), cai para o EV
    com a odd bruta e registra em log WARNING — sem quebrar o pipeline. Quando
    existe complemento apenas com limiar divergente, marca ``limiar_divergente``
    (contado separadamente para decidir interpolação futura).

    Com ``strict=True`` (pipeline real), mercado SEM par exato é DESCARTADO
    (retorna None): sem o resultado complementar não é possível remover o vig,
    e a odd bruta de um mercado órfão gera EV incomparável (fonte dos EV
    absurdos de 822%, 527%...). O mock mantém o fallback para o dado sintético.

    Args:
        prediction: Predição do modelo.
        offered_odd: Odd bruta do mercado avaliado.
        market: Nome do mercado avaliado (ex: "corners_over_9.5").
        odds_list: Lista de odds da mesma partida.
        strict: Se True, retorna None quando o par não existe.

    Returns:
        Tupla ``(ev_percent, vig_removed, limiar_divergente)``, ou None se
        ``strict`` e o par Over/Under não for encontrado.
    """
    complementary = _complementary_odd(market, odds_list)
    vig_used = complementary is not None
    divergent = (not vig_used) and _find_divergent_line(market, odds_list)
    vig_stats.record(vig_used)
    if divergent:
        vig_stats.record_divergent()
    if vig_used:
        return calculate_ev_vig_removed(prediction, offered_odd, complementary), True, False
    logger.warning(
        "complementary_odd_not_found",
        match_id=getattr(prediction, "match_id", None),
        market=market,
        fallback="skip" if strict else "raw_odd",
        limiar_divergente=divergent,
    )
    if strict:
        return None
    return calculate_ev(prediction, offered_odd), False, divergent


def _report_fallback_alert(
    stats=None,
    threshold: Optional[float] = None,
    level: Optional[str] = None,
    in_mock: bool = False,
) -> Optional[str]:
    """Avalia a taxa de fallback e loga alerta se exceder o limiar.

    Nível do log vem de `settings.fallback_alert_level` (ERROR default, INFO
    permitido). Em modo mock o alerta é SEMPRE INFO (o dado sintético não tem
    pares, então 100% de fallback é esperado e não é alarme).

    Args:
        stats: Contadores de vig (default: singleton global).
        threshold: % de fallback que dispara o alerta (default de settings).
        level: Nível do log (default de settings; 'ERROR' ou 'INFO').
        in_mock: True quando o pipeline rodou com dados mockados.

    Returns:
        Mensagem de alerta (exibível no console) ou None se abaixo do limiar.
    """
    if stats is None:
        stats = vig_stats
    if threshold is None:
        threshold = settings.fallback_warning_threshold
    if stats.total == 0 or stats.fallback_percent <= threshold:
        return None

    message = (
        f"ALERTA: {stats.fallback_percent:.1f}% das oportunidades usaram odd bruta "
        "(sem remoção de vig). Verificar coleta de pares Over/Under."
    )
    log_level = (level or settings.fallback_alert_level).upper()
    if in_mock or log_level == "INFO":
        logger.info(
            "vig_fallback_alert",
            message=message,
            fallback_percent=round(stats.fallback_percent, 1),
            with_vig=stats.with_vig,
            fallback=stats.fallback,
        )
    else:
        logger.error(
            "vig_fallback_alert",
            message=message,
            fallback_percent=round(stats.fallback_percent, 1),
            with_vig=stats.with_vig,
            fallback=stats.fallback,
        )
    return message


def _parse_market(market: str) -> Optional[Tuple[str, str, float]]:
    """Valida um mercado contra a whitelist estrita e a faixa aceita.

    O modelo Poisson calcula a probabilidade da PARTIDA INTEIRA para
    corners/cards/goals Over/Under. Qualquer outro formato (asiáticos,
    double chance, 1º tempo, jogador, etc.) é rejeitado aqui.

    Returns:
        Tupla (tipo, over_under, limiar) se aceito; None se rejeitado.
    """
    market_lower = market.lower()
    match = MARKET_PATTERN.match(market_lower)
    if not match:
        logger.warning("market_rejected", market=market, reason="not_in_whitelist")
        return None

    market_type, side, threshold_str = match.groups()
    threshold = float(threshold_str)
    min_threshold, max_threshold = MARKET_THRESHOLD_RANGES[market_type]

    if not (min_threshold <= threshold <= max_threshold):
        logger.warning(
            "market_rejected",
            market=market,
            reason="threshold_out_of_range",
            threshold=threshold,
            min_threshold=min_threshold,
            max_threshold=max_threshold,
        )
        return None

    return market_type, side, threshold


# =============================================================================
# Pipeline Mock
# =============================================================================
async def _run_mock_pipeline() -> List[Opportunity]:
    """Executa o pipeline com dados fictícios para teste offline.

    Returns:
        Lista de oportunidades encontradas.
    """
    from radar_ev.mock_data import get_mock_matches, get_mock_odds, get_mock_prediction

    logger.info("pipeline_mode", mode="mock")
    print("🧪 Usando dados mockados (sem API real).\n")

    opportunities: List[Opportunity] = []
    matches = get_mock_matches()

    for match in matches:
        odds_list = get_mock_odds(match.id)
        for odds in odds_list:
            pred = get_mock_prediction(match.id, odds.market)
            ev, vig_used, vig_divergente = _calculate_ev_without_vig(pred, odds.odd_value, odds.market, odds_list)

            market_amigavel = format_market_name(odds.market)
            odds.market = market_amigavel
            pred.market = market_amigavel

            if ev >= settings.min_ev_percent:
                opp = create_opportunity(
                    match=match,
                    prediction=pred,
                    odds=odds,
                    ev_percent=ev,
                    reasoning="Análise Estatística Avançada (Poisson)",
                    vig_removed=vig_used,
                    vig_divergente=vig_divergente,
                )
                ok, reason = apply_all_rules(
                    opp, settings.derby_teams_list, min_motivation=7.0
                )
                if ok:
                    opportunities.append(opp)
                    
                    # Salva no banco de dados
                    try:
                        from radar_ev.database import db
                        db.save_opportunity(opp)
                    except Exception as e:
                        logger.error("database_save_failed", error=str(e))
                        
                    print(f"  ✅ {opp.summary}")
                else:
                    print(f"  ❌ Filtrado: {reason}")
            else:
                print(
                    f"  ⚠️ EV baixo ({ev:.1f}%) — "
                    f"{match.home_team} vs {match.away_team} | {odds.market}"
                )

    return opportunities


# =============================================================================
# Pipeline Real (API)
# =============================================================================
async def _run_real_pipeline(league_filter: Optional[str] = None) -> List[Opportunity]:
    """Executa o pipeline com dados reais da API-Football.

    Fluxo:
    1. Busca partidas do dia.
    2. Filtra por ligas de interesse e janela de operação.
    3. Para cada partida: busca odds da Betano.
    4. Para cada odd: gera predição Poisson e calcula EV.
    5. Se EV >= limiar: aplica regras de negócio.
    6. Se aprovado: cria Opportunity.

    Args:
        league_filter: Se informado (--league), restringe as partidas a essa
            liga exata em vez da whitelist ``LIGAS_INTERESSE``.

    Returns:
        Lista de oportunidades encontradas.
    """
    from radar_ev.collectors.football_api import FootballAPICollector

    logger.info("pipeline_mode", mode="real")
    print("🌐 Usando APIs reais (Football API + Betano odds).\n")

    opportunities: List[Opportunity] = []
    rate_limit_hits = 0

    async with FootballAPICollector() as football:
        # 1. Busca partidas (hoje e próximos dias dependendo da janela)
        all_matches = await _fetch_future_matches(football)
        days_to_check = max(1, int(settings.pre_match_hours / 24) + 1)
        print(f"📅 Encontradas {len(all_matches)} partidas nos próximos {days_to_check} dia(s).")

        # 2. Filtra por ligas de interesse e janela de operação
        filtered_matches = _filter_matches(all_matches, league_filter)
        print(f"🎯 {len(filtered_matches)} partidas após filtro (ligas + janela).\n")

        if not filtered_matches:
            print("ℹ️ Nenhuma partida de interesse na janela de operação.")
            return opportunities

        # 3. Processa cada partida
        for i, match in enumerate(filtered_matches, 1):
            try:
                print(f"[{i}/{len(filtered_matches)}] {match.home_team} vs {match.away_team} ({match.league})")

                # Rate limit: pausa entre chamadas
                await asyncio.sleep(RATE_LIMIT_DELAY)

                # 3a. Busca odds da Betano E da Pinnacle (referência de preço)
                odds_list = await football.get_odds(match.id)
                if not odds_list:
                    print(f"  ⏭️ Sem odds da Betano — pulando.\n")
                    continue

                betano_odds = [o for o in odds_list if o.offered_by == "betano"]
                pinnacle_odds = [o for o in odds_list if o.offered_by == "pinnacle"]

                if not betano_odds:
                    print(f"  ⏭️ Sem odds da Betano — pulando.\n")
                    continue
                if not pinnacle_odds:
                    logger.warning(
                        "pinnacle_odds_missing",
                        match_id=match.id,
                        message="Pinnacle não publicou odds para este jogo — "
                                "seguindo apenas com a Betano.",
                    )

                print(f"  📊 {len(betano_odds)} odds Betano + {len(pinnacle_odds)} Pinnacle.")

                # 3b. Para cada odd, gera predição e calcula EV
                match_opps = await _process_match_odds(
                    match=match,
                    odds_list=betano_odds,
                    football_api=football,
                    pinnacle_odds=pinnacle_odds,
                )
                opportunities.extend(match_opps)
                print()

            except ApiError as exc:
                logger.error(
                    "api_football_api_error",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    message="Erro classificado da API — encerrando pipeline "
                            "(quota=2, plano=3, genérico=4).",
                )
                raise  # Não é crash nem retentável; encerra com código próprio

            except RateLimitError:
                rate_limit_hits += 1
                logger.warning("rate_limit_in_pipeline", hits=rate_limit_hits)
                print(f"  ⚠️ Rate limit atingido ({rate_limit_hits}x). Esperando 60s...")

                if rate_limit_hits >= MAX_RATE_LIMIT_RETRIES:
                    print("  🛑 Muitos rate limits. Interrompendo pipeline.")
                    break

                await asyncio.sleep(60)  # Espera 1 minuto

            except Exception as exc:
                logger.error(
                    "match_processing_failed",
                    match_id=match.id,
                    error=str(exc),
                )
                print(f"  💥 Erro ao processar: {exc}\n")
                continue

    return opportunities


async def _process_match_odds(
    match: Match,
    odds_list: list,
    football_api: object,
    pinnacle_odds: Optional[list] = None,
) -> List[Opportunity]:
    """Processa todas as odds de uma partida, gerando predições e filtrando por EV e regras.

    Args:
        match: Partida a ser processada.
        odds_list: Lista de odds da Betano.
        football_api: Instância do collector.
        pinnacle_odds: Lista de odds da Pinnacle (referência de preço justo).
            Quando há odd da Pinnacle para o mesmo mercado/linha, ela é salva
            em odds_timeline e em opportunities.pinnacle_reference_odd.

    Returns:
        Lista de oportunidades aprovadas.
    """
    opportunities: List[Opportunity] = []

    for odds in odds_list:
        # Gera predição baseada no tipo de mercado
        pred = await _get_prediction_for_market(
            match=match,
            market=odds.market,
            football_api=football_api,
        )

        if pred is None:
            continue

        # Referência da Pinnacle (antes do market ser convertido p/ nome amigável)
        pinnacle_ref = _find_pinnacle_reference(odds.market, pinnacle_odds)

        # Calcula EV (strict: sem par Over/Under exato, a odd bruta órfã
        # não gera oportunidade — EV incomparável, fonte dos EVs absurdos)
        ev_result = _calculate_ev_without_vig(
            pred, odds.odd_value, odds.market, odds_list, strict=True
        )
        if ev_result is None:
            continue
        ev, vig_used, vig_divergente = ev_result

        market_amigavel = format_market_name(odds.market)
        odds.market = market_amigavel
        pred.market = market_amigavel

        if ev >= settings.min_ev_percent:
            opp = create_opportunity(
                match=match,
                prediction=pred,
                odds=odds,
                ev_percent=ev,
                reasoning="Análise Estatística Avançada (Poisson)",
                vig_removed=vig_used,
                vig_divergente=vig_divergente,
            )

            # Aplica regras de negócio (Motivação 0.0 pois não temos o scraper integrado ainda)
            ok, reason = apply_all_rules(
                opp, settings.derby_teams_list, min_motivation=0.0
            )

            if ok:
                opportunities.append(opp)
                
                # Salva no banco de dados (inclui snapshot da odd + referência Pinnacle)
                try:
                    from radar_ev.database import db
                    db.save_opportunity(opp, pinnacle_reference_odd=pinnacle_ref)
                except Exception as e:
                    logger.error("database_save_failed", error=str(e))
                    
                print(f"  ✅ {opp.summary}")
                logger.info("opportunity_found", summary=opp.summary)
            else:
                print(f"  ❌ Filtrado: {reason}")
                logger.info("opportunity_filtered", reason=reason)
        else:
            logger.debug(
                "low_ev",
                market=odds.market,
                ev=round(ev, 1),
            )

    return opportunities


async def _get_prediction_for_market(
    match: Match,
    market: str,
    football_api: object,
) -> Optional[object]:
    """Gera predição Poisson para o mercado, se suportado (whitelist estrita).

    Fluxo:
    1. Valida o nome do mercado contra `MARKET_PATTERN` e a faixa de sanity.
       Se não casar OU estiver fora do range → None (sem chamar a API).
    2. Verifica se há IDs para buscar estatísticas. Sem IDs → None + log.
    3. Despacha para o predictor apropriado (corners/cards/goals).

    Args:
        match: Partida.
        market: Nome do mercado (ex: "corners_over_9.5").
        football_api: Instância do collector.

    Returns:
        Prediction ou None se o mercado não for suportado.
    """
    parsed = _parse_market(market)
    if parsed is None:
        return None
    market_type, side, threshold = parsed

    # Verifica se temos IDs necessários para buscar estatísticas
    has_ids = all([
        match.home_team_id,
        match.away_team_id,
        match.league_id,
        match.season,
    ])

    if not has_ids:
        logger.warning(
            "market_skipped",
            market=market,
            reason="missing_team_ids",
            match_id=match.id,
        )
        return None

    if market_type == "corners":
        await asyncio.sleep(RATE_LIMIT_DELAY)  # Rate limit para stats
        return await make_corners_prediction(
            match_id=match.id,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            league_id=match.league_id,
            season=match.season,
            football_api=football_api,
            threshold=threshold,
            is_over=(side == "over"),
        )

    elif market_type == "cards":
        await asyncio.sleep(RATE_LIMIT_DELAY)
        return await make_cards_prediction(
            match_id=match.id,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            league_id=match.league_id,
            season=match.season,
            football_api=football_api,
            threshold=threshold,
            is_over=(side == "over"),
        )

    else:  # goals
        await asyncio.sleep(RATE_LIMIT_DELAY)
        return await make_goals_prediction(
            match_id=match.id,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            league_id=match.league_id,
            season=match.season,
            football_api=football_api,
            threshold=threshold,
            is_over=(side == "over"),
        )


def _extract_threshold(market_name: str, default: float = 9.5) -> float:
    """Extrai o limiar numérico do nome do mercado.

    Ex: "corners_over_under_over_9.5" → 9.5
        "cards_over_4.5" → 4.5

    Args:
        market_name: Nome do mercado em lowercase.
        default: Valor padrão se não encontrar número.

    Returns:
        Limiar numérico.
    """
    import re

    # Procura por padrão numérico com ponto decimal
    match = re.search(r"(\d+\.?\d*)", market_name)
    if match:
        return float(match.group(1))
    return default


def _top_leagues(matches: List[Match], top_n: int = 10) -> List[dict]:
    """Top ligas por número de partidas (para sugestão no --league)."""
    counts: dict = {}
    for match in matches:
        counts[match.league] = counts.get(match.league, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": name, "count": count} for name, count in ranked[:top_n]]


def _filter_matches(matches: List[Match], league_filter: Optional[str] = None) -> List[Match]:
    """Filtra partidas por ligas de interesse e janela de operação.

    Args:
        matches: Lista completa de partidas do dia.
        league_filter: Se informado (--league), mantém APENAS partidas dessa
            liga exata, em vez da whitelist ``LIGAS_INTERESSE``.

    Returns:
        Lista filtrada de partidas relevantes.
    """
    now = datetime.now(timezone.utc)
    filtered = []

    for match in matches:
        # Verifica se a liga é de interesse (ou filtro explícito)
        if league_filter is not None:
            if match.league != league_filter:
                continue
        elif match.league not in LIGAS_INTERESSE:
            continue

        # Verifica janela de operação (pré-match)
        seconds_until = (match.datetime - now).total_seconds()
        if seconds_until < 0:
            continue  # Jogo já começou
        if seconds_until > settings.pre_match_hours * 3600:
            continue  # Jogo muito distante

        filtered.append(match)

    # Alerta operacional (Risco 4): nome de liga digitado errado → 0 matches.
    # Lista as ligas disponíveis NESTE run como sugestão.
    if league_filter is not None and not filtered:
        message = (
            f"Nenhum match encontrado para --league='{league_filter}'. "
            "Verifique o nome exato da liga na API."
        )
        logger.warning(
            "league_filter_no_matches",
            league=league_filter,
            suggestions=[c["name"] for c in _top_leagues(matches)],
            message=message,
        )
        print(f"⚠️ {message}")
        suggestions = _top_leagues(matches, top_n=10)
        if suggestions:
            print("Ligas disponíveis neste run (top 10):")
            for rank, item in enumerate(suggestions, 1):
                print(f"  {rank}. {item['name']} ({item['count']} matches)")

    return filtered


async def _fetch_future_matches(football, days_to_check: Optional[int] = None) -> List[Match]:
    """Busca partidas da janela de operação, priorizando coleta por liga.

    Com ligas configuradas (``settings.LEAGUES``), busca **por liga** via
    ``/fixtures?league=X&season=SEASON&from=today&to=tomorrow`` — evita perder
    jogos que passam da meia-noite e sinaliza a liga de cada jogo. O resultado
    é deduplicado por ``fixture.id`` (um jogo pode aparecer em várias ligas).
    Se uma liga falhar, as demais seguem (não aborta); erros de conta
    (quota/plano) continuam abortando com o exit code próprio.

    Sem ligas configuradas, cai no comportamento legado: busca por data
    (/fixtures?date=) para cada dia da janela.

    Args:
        football: Instância do FootballAPICollector.
        days_to_check: Nº de dias da janela (default de settings.pre_match_hours).

    Returns:
        Lista de partidas únicas (por id) de todas as ligas/datas.
    """
    days_to_check = days_to_check or max(1, int(settings.pre_match_hours / 24) + 1)
    today = datetime.now(timezone.utc)
    all_matches: List[Match] = []
    seen_ids: set = set()

    league_ids = list(settings.leagues)
    if league_ids:
        season = settings.season
        date_to = today + timedelta(days=max(0, days_to_check - 1))
        for league_id in league_ids:
            try:
                league_matches = await football.get_league_matches(
                    league_id, season, today, date_to
                )
            except (ApiQuotaExhaustedError, ApiPlanInsufficientError):
                # Erro global de conta: não tem por que continuar nas outras ligas.
                raise
            except ApiError as exc:
                logger.warning(
                    "league_fetch_api_error",
                    league_id=league_id,
                    error=str(exc),
                    message="Liga falhou (erro classificado) — seguindo para as demais.",
                )
                continue
            except Exception as exc:
                logger.warning(
                    "league_fetch_failed",
                    league_id=league_id,
                    error=str(exc),
                )
                continue

            for match in league_matches:
                if match.id in seen_ids:
                    continue
                seen_ids.add(match.id)
                all_matches.append(match)
    else:
        # Fallback legado: coleta por data (sem correlação por liga).
        for d in range(days_to_check):
            target_date = today + timedelta(days=d)
            matches_for_day = await football.get_today_matches(target_date)
            all_matches.extend(matches_for_day)

    logger.info(
        "future_matches_fetched",
        count=len(all_matches),
        by_league=bool(league_ids),
        days=days_to_check,
    )
    return all_matches


async def _list_leagues(use_mock: bool = False, league_filter: Optional[str] = None) -> None:
    """Lista as ligas disponíveis (modo --list-leagues) e não roda o pipeline.

    Em modo real usa a mesma janela/busca do pipeline; em mock usa os times
    mockados. Se um ``--league`` for passado junto, o filtro é aplicado para
    confirmar o nome antes de sair.
    """
    if use_mock:
        from radar_ev.mock_data import get_mock_matches

        matches = get_mock_matches()
    else:
        from radar_ev.collectors.football_api import FootballAPICollector

        async with FootballAPICollector() as football:
            matches = await _fetch_future_matches(football)

    if league_filter is not None:
        filtered = [m for m in matches if m.league == league_filter]
        if not filtered:
            _filter_matches(matches, league_filter=league_filter)
        else:
            print(f"✅ Liga '{league_filter}' encontrada: {len(filtered)} partida(s) na janela.")
        return

    top = _top_leagues(matches, top_n=10)
    print("Ligas disponíveis (--league aceita):")
    if not top:
        print("  (nenhuma partida encontrada na janela)")
    for rank, item in enumerate(top, 1):
        print(f"  {rank}. {item['name']} ({item['count']} matches)")


# =============================================================================
# Entry Point
# =============================================================================
async def run_pipeline(
    use_mock: bool = False,
    league_filter: Optional[str] = None,
) -> List[Opportunity]:
    """Executa o pipeline completo do Radar +EV.

    Args:
        use_mock: Se True, usa dados fictícios. Se False, usa APIs reais.
        league_filter: Se informado (--league), restringe as partidas a essa
            liga exata (modo real).

    Returns:
        Lista de oportunidades encontradas.
    """
    # Inicializa logging estruturado
    setup_logging(log_level="INFO", json_output=False)

    # Zera contadores de vig desta execução
    vig_stats.reset()

    # Zera o contador de requisições à API desta execução
    reset_api_request_count()

    print("=" * 60)
    print("🎯 RADAR +EV — Sistema de Recomendação Pré-Jogo")
    print(f"📅 Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⚙️ EV mínimo: {settings.min_ev_percent}%")
    print(f"⏰ Janela: {settings.pre_match_hours}h antes do jogo")
    print("=" * 60)
    print()

    logger.info(
        "pipeline_started",
        use_mock=use_mock,
        min_ev=settings.min_ev_percent,
        pre_match_hours=settings.pre_match_hours,
    )

    try:
        # Executa pipeline (mock ou real)
        if use_mock:
            opportunities = await _run_mock_pipeline()
        else:
            opportunities = await _run_real_pipeline(league_filter=league_filter)

        # Resumo
        print()
        print("=" * 60)
        print(f"📋 RESUMO: {len(opportunities)} oportunidade(s) encontrada(s)")
        print("=" * 60)

        # Monitora taxa de fallback de pares Over/Under (vig removido vs. odd bruta)
        if vig_stats.total:
            print(
                f"🔎 Vig: {vig_stats.with_vig} com par ({vig_stats.vig_percent:.1f}%) | "
                f"{vig_stats.fallback} fallback ({vig_stats.fallback_percent:.1f}%)"
            )
        fallback_alert = _report_fallback_alert(in_mock=use_mock)
        if fallback_alert:
            print(f"🔔 {fallback_alert}")

        for opp in opportunities:
            print(f"  🎯 {opp.summary}")

        # Envia via Telegram
        if opportunities:
            print(f"\n📤 Enviando {len(opportunities)} oportunidade(s) via Telegram...")
            try:
                sender = TelegramSender()
                sent = await sender.send_opportunities(opportunities)
                print(f"✅ {sent} mensagem(ns) enviada(s) com sucesso.")
            except Exception as exc:
                logger.error("telegram_failed", error=str(exc))
                print(f"⚠️ Erro ao enviar Telegram: {exc}")
        else:
            print("\nℹ️ Nenhuma oportunidade encontrada. Pipeline encerrado.")

        logger.info(
            "pipeline_finished",
            opportunities_count=len(opportunities),
        )
        logger.info("api_requests_used", count=get_api_request_count())
        return opportunities

    except ApiError:
        # Erro classificado de API (quota/plano/genérico): NÃO é um crash do
        # pipeline (não conta como _pipeline_crashed). Propaga para main()
        # mapear para o exit code próprio (2/3/4).
        raise

    except Exception as exc:
        global _pipeline_crashed
        _pipeline_crashed = True
        logger.exception("pipeline_crashed", error=str(exc))
        logger.info("api_requests_used", count=get_api_request_count())
        print(f"\n💥 ERRO CRÍTICO: {exc}")
        return []


def _count_resolved(resolver, pending_rows: list) -> dict:
    """Conta o desfecho dos PENDING após a resolução, em 4 categorias.

    Categorias:
    - ``greens``: RESOLVED com ``result_won == 1`` (GREEN).
    - ``reds``: RESOLVED com ``result_won == 0`` (RED).
    - ``nulls``: RESOLVED mas ``result_won IS NULL`` (cancelado/adiado/dado
      incompleto — NÃO conta como RED, para não inflar a taxa de perda).
    - ``still_pending``: continuam sem RESOLVED.

    Returns:
        Dict com essas 4 contagens.
    """
    ids = [row["id"] for row in pending_rows]
    if not ids:
        return {"greens": 0, "reds": 0, "nulls": 0, "still_pending": 0}
    placeholders = ",".join("?" * len(ids))
    with get_db_connection(resolver.db_path) as conn:
        row = conn.execute(
            f"SELECT "
            f"SUM(CASE WHEN status = 'RESOLVED' AND result_won = 1 THEN 1 ELSE 0 END) AS greens, "
            f"SUM(CASE WHEN status = 'RESOLVED' AND result_won = 0 THEN 1 ELSE 0 END) AS reds, "
            f"SUM(CASE WHEN status = 'RESOLVED' AND result_won IS NULL THEN 1 ELSE 0 END) AS nulls, "
            f"SUM(CASE WHEN status != 'RESOLVED' THEN 1 ELSE 0 END) AS still_pending "
            f"FROM opportunities WHERE id IN ({placeholders})",
            ids,
        ).fetchone()
    return {
        "greens": row["greens"] or 0,
        "reds": row["reds"] or 0,
        "nulls": row["nulls"] or 0,
        "still_pending": row["still_pending"] or 0,
    }


async def _run_result_resolution() -> dict:
    """Resolve oportunidades PENDING via result_resolver e loga estruturado.

    O resolver já envia o relatório diário ao Telegram internamente quando há
    resoluções. Aqui apenas medimos PENDING/GREEN/RED/NULL para telemetria,
    e emitimos WARNING se a taxa de RESOLVED indeterminados (NULL) exceder o
    limiar de settings (default 5%).

    Returns:
        Dict com as contagens ``{pending, greens, reds, nulls, still_pending}``.
    """
    from radar_ev.result_resolver import ResultResolver

    reset_api_request_count()

    resolver = ResultResolver()
    pending_rows = resolver.get_pending_opportunities()
    pending = len(pending_rows)
    logger.info("result_resolution_started", pending=pending)
    print(f"🔍 Resultados: {pending} oportunidade(s) PENDING encontrada(s).")
    if pending == 0:
        logger.info("api_requests_used", count=get_api_request_count())
        return {"pending": 0, "greens": 0, "reds": 0, "nulls": 0, "still_pending": 0}

    await resolver.resolve_all()

    counts = _count_resolved(resolver, pending_rows)
    resolved = counts["greens"] + counts["reds"] + counts["nulls"]
    null_percent = (counts["nulls"] * 100.0 / resolved) if resolved else 0.0
    logger.info(
        "result_resolution_summary",
        pending=pending,
        greens=counts["greens"],
        reds=counts["reds"],
        nulls=counts["nulls"],
        still_pending=counts["still_pending"],
        resolved=resolved,
    )
    if resolved and null_percent > settings.result_resolution_null_threshold:
        message = (
            f"{null_percent:.1f}% das resoluções ficaram indeterminadas (NULL). "
            "Verificar cancelamentos/adiamentos."
        )
        logger.warning(
            "result_resolution_null_high",
            nulls=counts["nulls"],
            resolved=resolved,
            null_percent=round(null_percent, 1),
            message=message,
        )
        print(f"⚠️ {message}")
    print(
        f"✅ Resolvidas: {resolved} (GREEN={counts['greens']} RED={counts['reds']} "
        f"NULL={counts['nulls']})."
    )
    logger.info("api_requests_used", count=get_api_request_count())
    return {"pending": pending, "resolved": resolved, **counts}


def build_arg_parser() -> argparse.ArgumentParser:
    """Constrói o parser do CLI (argparse) do orchestrator."""
    parser = argparse.ArgumentParser(
        prog="python -m radar_ev.orchestrator",
        description="Radar +EV — Sistema de Recomendação Pré-Jogo",
    )
    parser.add_argument(
        "--mock", "-m",
        action="store_true",
        help="Usar dados mockados (sem API real; nunca resolve resultados)",
    )
    parser.add_argument(
        "--daemon", "-d",
        action="store_true",
        help="Executar continuamente em loop infinito (para nuvem/Docker). "
             "Resolve resultados antes de cada ciclo",
    )
    parser.add_argument(
        "--resolve", "-r",
        action="store_true",
        help="Executar APENAS a resolução de resultados (sem buscar jogos). "
             "Ignorado em modo mock",
    )
    parser.add_argument(
        "--no-resolve", "-nr",
        action="store_true",
        help="Pular a resolução de resultados no ciclo do pipeline"
             " (principalmente para o daemon)",
    )
    parser.add_argument(
        "--league", "-l",
        type=str,
        default=None,
        metavar="LIGA",
        help="Filtrar o pipeline para uma liga específica (ex: 'Premier "
             "League'; apenas modo real)",
    )
    parser.add_argument(
        "--list-leagues",
        action="store_true",
        help="Listar as ligas disponíveis na janela e sair (não roda o "
             "pipeline). Combinável com --league para validar o nome",
    )
    return parser


async def _daemon_loop(
    resolve_in_cycle: bool,
    use_mock: bool,
    league_filter: Optional[str],
) -> None:
    """Loop infinito do modo daemon: resolve e depois roda o pipeline."""
    print("🚀 Iniciando Radar +EV em modo DAEMON (Loop Contínuo)...")
    while True:
        try:
            if resolve_in_cycle:
                await _run_result_resolution()
            await run_pipeline(use_mock=use_mock, league_filter=league_filter)
            print(f"⏳ Aguardando 1 hora para o próximo ciclo...")
            await asyncio.sleep(3600)  # 1 hora
        except KeyboardInterrupt:
            print("🛑 Daemon interrompido pelo usuário.")
            break
        except ApiError:
            print("🛑 Erro classificado da API em ciclo — encerrando daemon (exit 2/3/4).")
            raise  # Propaga para main() mapear o exit code; não adianta esperar 5 min
        except Exception as e:
            print(f"💥 Erro no daemon: {e}")
            print(f"⏳ Tentando novamente em 5 minutos...")
            await asyncio.sleep(300)


async def _execute_cli(args) -> None:
    """Executa as ações do CLI num loop já existente (testável).

    ``main()`` apenas faz o parse e chama ``asyncio.run(_execute_cli(args))``.

    Regras de orquestração:
    - Single-shot: NÃO resolve por padrão (só o pipeline).
    - ``--daemon``: resolve antes de cada ciclo (exceto com ``--no-resolve``).
    - ``--resolve``: executa APENAS a resolução e termina.
    - Modo mock: resolução SEMPRE desabilitada (não toca o banco real).
    """
    use_mock = args.mock
    run_daemon = args.daemon

    # --list-leagues: apenas lista as ligas disponíveis e sai (não roda pipeline).
    if args.list_leagues:
        await _list_leagues(use_mock=use_mock, league_filter=args.league)
        return

    # Salvaguarda (isolação dev/prod): mock NUNCA resolve resultados. O INFO
    # é emitido só uma vez por processo para não poluir o daemon (Risco 5).
    global _logged_mock_resolution_disabled
    if use_mock and (args.resolve or args.no_resolve or run_daemon):
        if not _logged_mock_resolution_disabled:
            logger.info(
                "mock_resolution_disabled",
                message="Modo mock: resolução desabilitada automaticamente.",
            )
            _logged_mock_resolution_disabled = True

    only_resolve = args.resolve and not use_mock
    resolve_in_cycle = run_daemon and not use_mock and not args.no_resolve

    if only_resolve:
        await _run_result_resolution()
    elif run_daemon:
        await _daemon_loop(
            resolve_in_cycle=resolve_in_cycle,
            use_mock=use_mock,
            league_filter=args.league,
        )
    else:
        await run_pipeline(use_mock=use_mock, league_filter=args.league)


def main(argv: Optional[list] = None) -> int:
    """Entry point para execução via CLI ou Poetry script.

    Returns:
        Código de saída:
        0 — sucesso.
        1 — pipeline quebrou (``pipeline_crashed``) ou exceção não tratada.
        2 — cota diária da API-Football esgotada.
        3 — plano da API-Football sem acesso (ex: temporada bloqueada).
        4 — erro genérico classificado da API-Football (errors no JSON).
    """
    global _pipeline_crashed
    _pipeline_crashed = False
    args = build_arg_parser().parse_args(argv)
    try:
        asyncio.run(_execute_cli(args))
    except ApiQuotaExhaustedError as exc:
        logger.error(
            "api_football_quota_exhausted",
            message="Cota diária da API-Football esgotada. Encerrando (exit 2).",
            error=str(exc),
        )
        print(f"\n🛑 COTA DIÁRIA ESGOTADA (exit 2): {exc}")
        return 2
    except ApiPlanInsufficientError as exc:
        logger.error(
            "api_football_plan_insufficient",
            message="Plano da API-Football sem acesso. Encerrando (exit 3).",
            error=str(exc),
        )
        print(f"\n🛑 PLANO INSUFICIENTE (exit 3): {exc}")
        return 3
    except ApiError as exc:
        logger.error(
            "api_football_error",
            message="Erro genérico da API-Football. Encerrando (exit 4).",
            error=str(exc),
        )
        print(f"\n🛑 ERRO DA API-FOOTBALL (exit 4): {exc}")
        return 4
    except Exception as exc:
        logger.exception("cli_crashed", error=str(exc))
        print(f"\n💥 ERRO CRÍTICO: {exc}")
        return 1
    return 1 if _pipeline_crashed else 0


if __name__ == "__main__":
    sys.exit(main())
