"""
backtester.py — Motor de Backtesting para o Radar +EV.

Analisa as oportunidades salvas no Data Lake (history.db) e calcula
métricas de desempenho do sistema para provar lucratividade ao longo do tempo.

Validações adicionais:
- Holdout temporal (70/30 por match_date) para detectar sobreajuste.
- Tamanho mínimo de amostra para a conclusão ser estatisticamente defensável.
"""

import math
import sqlite3
from pathlib import Path
from typing import Any

import structlog

from radar_ev.db import get_db_connection, turso_configured

logger = structlog.get_logger(__name__)


def _row_value(row: object, key: str, default: Any = 0.0) -> Any:
    """Lê um valor de linha (sqlite3.Row / TursoRow / dict) com fallback."""
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, ValueError, TypeError):
        return default


def minimum_sample_size(
    expected_roi: float,
    std_dev: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> float:
    """Tamanho mínimo da amostra para detectar um ROI esperado.

    Fórmula clássica para teste bilateral de média:
        n = ((z_alpha + z_beta) * std_dev / expected_roi) ** 2
    com z_alpha = 1.96 (alpha=0.05) e z_beta = 0.84 (power=0.80),
    derivados da normal (scipy) quando alpha/power diferem do default.

    Args:
        expected_roi: ROI esperado (% por aposta).
        std_dev: Desvio padrão amostral do retorno por aposta (%).
        alpha: Nível de significância (bilateral).
        power: Poder estatístico desejado.

    Returns:
        n mínimo arredondado para cima; ``inf`` se o ROI esperado for
        não-positivo ou sem dispersão (não há amostra que garanta).
    """
    if expected_roi <= 0 or std_dev <= 0:
        return math.inf

    from scipy import stats

    z_alpha = stats.norm.ppf(1.0 - alpha / 2.0)
    z_beta = stats.norm.ppf(power)
    n = ((z_alpha + z_beta) * std_dev / expected_roi) ** 2
    return float(math.ceil(n))


def _resolved_rows(rows: list) -> list:
    """Filtra apenas as oportunidades RESOLVIDAS (apostas com desfecho)."""
    return [
        r for r in rows
        if str(_row_value(r, "status", "")).upper() == "RESOLVED"
    ]


def compute_metrics(rows: list) -> dict:
    """ROI, hit rate, EV médio e n_apostas para um conjunto de oportunidades.

    Só apostas RESOLVIDAS entram no ROI/hit rate (lucro real sobre stake);
    o EV médio considera todas as oportunidades da amostra.
    """
    resolved = _resolved_rows(rows)
    n = len(resolved)

    avg_ev = (
        sum(float(_row_value(r, "ev_percent", 0.0)) for r in rows) / len(rows)
        if rows
        else 0.0
    )
    if n == 0:
        return {"roi": 0.0, "hit_rate": 0.0, "avg_ev": avg_ev, "n": 0, "stake": 0.0}

    stakes = 0.0
    profit = 0.0
    wins = 0
    for r in resolved:
        stake = float(_row_value(r, "recommended_stake", 0.0) or 0.0)
        if stake <= 0:
            stake = 1.0  # mesma convenção do resolver: 1% padrão
        stakes += stake
        profit += float(_row_value(r, "profit", 0.0) or 0.0)
        if _row_value(r, "result_won", 0):
            wins += 1

    roi = (profit / stakes * 100.0) if stakes else 0.0
    hit_rate = wins / n * 100.0
    return {"roi": roi, "hit_rate": hit_rate, "avg_ev": avg_ev, "n": n, "stake": stakes}


def roi_std_dev(rows: list) -> float:
    """Desvio padrão amostral do retorno por aposta (%), base para sample size."""
    returns = []
    for r in _resolved_rows(rows):
        stake = float(_row_value(r, "recommended_stake", 0.0) or 0.0) or 1.0
        returns.append(float(_row_value(r, "profit", 0.0) or 0.0) / stake * 100.0)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
    return math.sqrt(var)


def compute_holdout(rows: list) -> tuple[dict, dict]:
    """Split temporal cronológico 70% (treino, mais antigas) / 30% (teste, mais recentes).

    Returns:
        (métricas de treino, métricas de teste).
    """
    ordered = sorted(rows, key=lambda r: str(_row_value(r, "match_date", "")))
    split = int(len(ordered) * 0.7)
    if ordered and split == 0:
        split = 1
    train_rows = ordered[:split]
    test_rows = ordered[split:]
    return compute_metrics(train_rows), compute_metrics(test_rows)


def possible_overfitting(train: dict, test: dict) -> bool:
    """ROI_teste < ROI_treino × 0.5 → suspeita de overfitting."""
    if train["n"] == 0 or test["n"] == 0:
        return False
    return bool(test["roi"] < train["roi"] * 0.5)

def run_backtest(db_path: str = "data/history.db"):
    """Lê os dados do banco de dados e exibe um relatório financeiro."""

    # Verifica se o arquivo existe (modo local) antes de tentar abrir
    if not turso_configured() and not Path(db_path).exists():
        print("============================================================")
        print("📈 RELATÓRIO DE BACKTESTING - RADAR +EV")
        print("============================================================")
        print("Nenhum dado histórico encontrado. O sistema precisa rodar e")
        print("salvar oportunidades no banco de dados primeiro.")
        print("Execute o orquestrador ao menos uma vez: python -m radar_ev.orchestrator --mock")
        return

    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()

        # Lê todas as oportunidades do banco
        cursor.execute("SELECT * FROM opportunities")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("============================================================")
            print("📈 RELATÓRIO DE BACKTESTING - RADAR +EV")
            print("============================================================")
            print("Nenhum dado histórico encontrado. O sistema precisa rodar e")
            print("salvar oportunidades no banco de dados primeiro.")
            return

        ev_total = sum(r['ev_percent'] for r in rows)
        odd_total = sum(r['offered_odd'] for r in rows)
        stake_total = sum(r['recommended_stake'] for r in rows)

        count = len(rows)

        print("============================================================")
        print("📈 RELATÓRIO DE BACKTESTING - RADAR +EV")
        print("============================================================")
        print(f"Total de Oportunidades Encontradas: {count}")
        print(f"Média de Valor Esperado (EV): {ev_total / count:.2f}%")
        print(f"Média de Odd Oferecida: {odd_total / count:.2f}")
        print(f"Banca Sugerida Média (Kelly): {stake_total / count:.2f}%")
        print("------------------------------------------------------------")

        # Simulação de Lucratividade Teórica
        print("💰 Projeção Financeira Teórica (Baseada no EV):")

        banca_inicial = 1000.0
        banca_atual = banca_inicial

        for row in rows:
            stake_pct = row['recommended_stake'] / 100.0
            valor_aposta = banca_atual * stake_pct

            # O lucro esperado de uma aposta é (Valor Apostado * EV%)
            lucro_esperado = valor_aposta * (row['ev_percent'] / 100.0)
            banca_atual += lucro_esperado

        roi = ((banca_atual - banca_inicial) / banca_inicial) * 100

        print(f"Banca Inicial (Simulada): R$ {banca_inicial:.2f}")
        print(f"Banca Final Projetada: R$ {banca_atual:.2f}")
        print(f"ROI Esperado: {roi:.2f}%")
        print("============================================================")
        print("Nota: Para lucro real, integre com a API de /fixtures/events para")
        print("checar quem venceu e usar a coluna result_won.")

        # ==================================================================
        # Holdout temporal: separar treino (70% mais antigas) de teste (30%
        # mais recentes) evita validar o modelo nos MESMOS dados de ajuste.
        # ==================================================================
        train, test = compute_holdout(rows)
        overfit = possible_overfitting(train, test)
        if overfit:
            logger.warning(
                "possible_overfitting",
                train_roi=round(train["roi"], 2),
                test_roi=round(test["roi"], 2),
                train_n=train["n"],
                test_n=test["n"],
            )
        verdict = "POSSIBLE_OVERFITTING" if overfit else "OK"
        print("=== HOLDOUT ===")
        print(f"Treino: ROI={train['roi']:.2f}% (n={train['n']})")
        print(f"Teste:  ROI={test['roi']:.2f}% (n={test['n']})")
        print(f"Veredito: {verdict}")

        # ==================================================================
        # Tamanho mínimo de amostra: com 2.8 = z(0.975)+z(0.80), concluir
        # ROI>0 com menos apostas que o mínimo é estatisticamente frágil.
        # ==================================================================
        overall = compute_metrics(rows)
        std_dev = roi_std_dev(rows)
        n_min = minimum_sample_size(overall["roi"], std_dev)
        print(f"Sample atual: {overall['n']}")
        n_min_label = "inf" if math.isinf(n_min) else str(n_min)
        print(f"Sample mínimo para detectar ROI={overall['roi']:.2f}%: {n_min_label}")
        sufficient = overall["n"] >= n_min
        print(f"Veredito: {'AMOSTRA SUFICIENTE' if sufficient else 'AMOSTRA INSUFICIENTE'}")
        print("============================================================")

    except sqlite3.Error as e:
        print(f"Erro ao acessar o banco de dados: {e}")
    except Exception as e:
        print(f"Erro durante o backtest: {e}")

if __name__ == "__main__":
    run_backtest()
