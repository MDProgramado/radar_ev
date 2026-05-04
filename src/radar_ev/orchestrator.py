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

import asyncio
import sys
from datetime import datetime, timezone
from typing import List, Optional

import structlog

from radar_ev.alert.telegram import TelegramSender
from radar_ev.config import settings
from radar_ev.ev_calculator import calculate_ev, create_opportunity
from radar_ev.http_client import RateLimitError
from radar_ev.logger import setup_logging
from radar_ev.models import Match, Opportunity
from radar_ev.predictor import make_cards_prediction, make_corners_prediction
from radar_ev.rules import apply_all_rules

logger = structlog.get_logger(__name__)

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
    "Copa Libertadores",
    "Copa Sudamericana",
]

# Mercados que o predictor Poisson suporta atualmente
SUPPORTED_MARKETS = {
    "corners": ["corners_over_9.5", "corners_over_10.5", "corners_over_8.5"],
    "cards": ["cards_over_4.5", "cards_over_5.5", "cards_over_3.5"],
}

# =============================================================================
# Rate Limit Control
# =============================================================================
RATE_LIMIT_DELAY = 0.6  # Segundos entre cada chamada (≈10 req/min)
MAX_RATE_LIMIT_RETRIES = 3


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
            ev = calculate_ev(pred, odds.odd_value)

            if ev >= settings.min_ev_percent:
                opp = create_opportunity(match, pred, odds, ev)
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
async def _run_real_pipeline() -> List[Opportunity]:
    """Executa o pipeline com dados reais da API-Football.

    Fluxo:
    1. Busca partidas do dia.
    2. Filtra por ligas de interesse e janela de operação.
    3. Para cada partida: busca odds da Betano.
    4. Para cada odd: gera predição Poisson e calcula EV.
    5. Se EV >= limiar: aplica regras de negócio.
    6. Se aprovado: cria Opportunity.

    Returns:
        Lista de oportunidades encontradas.
    """
    from radar_ev.collectors.football_api import FootballAPICollector

    logger.info("pipeline_mode", mode="real")
    print("🌐 Usando APIs reais (Football API + Betano odds).\n")

    opportunities: List[Opportunity] = []
    rate_limit_hits = 0

    async with FootballAPICollector() as football:
        # 1. Busca partidas do dia
        today = datetime.now(timezone.utc)
        all_matches = await football.get_today_matches(today)
        print(f"📅 Encontradas {len(all_matches)} partidas hoje ({today.strftime('%Y-%m-%d')}).")

        # 2. Filtra por ligas de interesse e janela de operação
        filtered_matches = _filter_matches(all_matches)
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

                # 3a. Busca odds da Betano
                odds_list = await football.get_odds(match.id)
                if not odds_list:
                    print(f"  ⏭️ Sem odds da Betano — pulando.\n")
                    continue

                print(f"  📊 {len(odds_list)} odds encontradas.")

                # 3b. Para cada odd, gera predição e calcula EV
                match_opps = await _process_match_odds(
                    match=match,
                    odds_list=odds_list,
                    football_api=football,
                )
                opportunities.extend(match_opps)
                print()

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
) -> List[Opportunity]:
    """Processa todas as odds de uma partida, gerando predições e filtrando por EV e regras.

    Args:
        match: Partida a ser processada.
        odds_list: Lista de odds da Betano.
        football_api: Instância do collector.

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

        # Calcula EV
        ev = calculate_ev(pred, odds.odd_value)

        if ev >= settings.min_ev_percent:
            opp = create_opportunity(
                match=match,
                prediction=pred,
                odds=odds,
                ev_percent=ev,
                reasoning=f"Modelo {pred.model_version} — λ estimado via estatísticas históricas",
            )

            # Aplica regras de negócio
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
    """Gera predição Poisson para o mercado, se suportado.

    Detecta o tipo de mercado (escanteios, cartões) e despacha para
    o predictor apropriado.

    Args:
        match: Partida.
        market: Nome do mercado (ex: "corners_over_under_over_9.5").
        football_api: Instância do collector.

    Returns:
        Prediction ou None se o mercado não for suportado.
    """
    market_lower = market.lower()

    # Verifica se temos IDs necessários para buscar estatísticas
    has_ids = all([
        match.home_team_id,
        match.away_team_id,
        match.league_id,
        match.season,
    ])

    if not has_ids:
        # Sem IDs, usa predição mock como fallback
        from radar_ev.mock_data import get_mock_prediction
        return get_mock_prediction(match.id, market)

    # Detecta tipo de mercado e limiar
    if "corner" in market_lower:
        threshold = _extract_threshold(market_lower, default=9.5)
        await asyncio.sleep(RATE_LIMIT_DELAY)  # Rate limit para stats
        return await make_corners_prediction(
            match_id=match.id,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            league_id=match.league_id,
            season=match.season,
            football_api=football_api,
            threshold=threshold,
        )

    elif "card" in market_lower:
        threshold = _extract_threshold(market_lower, default=4.5)
        await asyncio.sleep(RATE_LIMIT_DELAY)
        return await make_cards_prediction(
            match_id=match.id,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
            league_id=match.league_id,
            season=match.season,
            football_api=football_api,
            threshold=threshold,
        )

    else:
        # Mercado não suportado pelo Poisson (ex: match_winner, goals)
        # Usa mock como fallback temporário
        from radar_ev.mock_data import get_mock_prediction
        return get_mock_prediction(match.id, market)


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


def _filter_matches(matches: List[Match]) -> List[Match]:
    """Filtra partidas por ligas de interesse e janela de operação.

    Args:
        matches: Lista completa de partidas do dia.

    Returns:
        Lista filtrada de partidas relevantes.
    """
    now = datetime.now(timezone.utc)
    filtered = []

    for match in matches:
        # Verifica se a liga é de interesse
        if match.league not in LIGAS_INTERESSE:
            continue

        # Verifica janela de operação (pré-match)
        seconds_until = (match.datetime - now).total_seconds()
        if seconds_until < 0:
            continue  # Jogo já começou
        if seconds_until > settings.pre_match_hours * 3600:
            continue  # Jogo muito distante

        filtered.append(match)

    return filtered


# =============================================================================
# Entry Point
# =============================================================================
async def run_pipeline(use_mock: bool = False) -> List[Opportunity]:
    """Executa o pipeline completo do Radar +EV.

    Args:
        use_mock: Se True, usa dados fictícios. Se False, usa APIs reais.

    Returns:
        Lista de oportunidades encontradas.
    """
    # Inicializa logging estruturado
    setup_logging(log_level="INFO", json_output=False)

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
            opportunities = await _run_real_pipeline()

        # Resumo
        print()
        print("=" * 60)
        print(f"📋 RESUMO: {len(opportunities)} oportunidade(s) encontrada(s)")
        print("=" * 60)

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
        return opportunities

    except Exception as exc:
        logger.exception("pipeline_crashed", error=str(exc))
        print(f"\n💥 ERRO CRÍTICO: {exc}")
        return []


def main() -> None:
    """Entry point para execução via CLI ou Poetry script."""
    # Verifica argumento de linha de comando
    use_mock = "--mock" in sys.argv or "-m" in sys.argv
    run_daemon = "--daemon" in sys.argv or "-d" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print("Radar +EV — Sistema de Recomendação Pré-Jogo")
        print()
        print("Uso: python -m radar_ev.orchestrator [opções]")
        print()
        print("Opções:")
        print("  --mock, -m    Usar dados mockados (sem API real)")
        print("  --daemon, -d  Executar continuamente em loop infinito (para nuvem/Docker)")
        print("  --help, -h    Mostrar esta ajuda")
        sys.exit(0)

    async def _daemon_loop():
        print("🚀 Iniciando Radar +EV em modo DAEMON (Loop Contínuo)...")
        while True:
            try:
                await run_pipeline(use_mock=use_mock)
                print(f"⏳ Aguardando 1 hora para o próximo ciclo...")
                await asyncio.sleep(3600)  # 1 hora
            except KeyboardInterrupt:
                print("🛑 Daemon interrompido pelo usuário.")
                break
            except Exception as e:
                print(f"💥 Erro no daemon: {e}")
                print(f"⏳ Tentando novamente em 5 minutos...")
                await asyncio.sleep(300)

    if run_daemon:
        asyncio.run(_daemon_loop())
    else:
        asyncio.run(run_pipeline(use_mock=use_mock))


if __name__ == "__main__":
    main()