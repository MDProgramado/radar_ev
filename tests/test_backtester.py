"""
test_backtester.py — Testes do motor de backtesting.

Cobre o holdout temporal (70/30 por match_date, sem validação nos mesmos
dados) e o tamanho mínimo de amostra para concluir ROI com poder estatístico.
"""

import math
import sqlite3

import pytest

from radar_ev.backtester import (
    compute_holdout,
    compute_metrics,
    minimum_sample_size,
    possible_overfitting,
    roi_std_dev,
)


def _opp(make_id: int) -> dict:
    """Oportunidade resolvida: GREEN com lucro pequeno."""
    return {
        "id": make_id,
        "match_id": make_id,
        "match_date": f"2025-01-{make_id:02d}T12:00:00+00:00",
        "status": "RESOLVED",
        "result_won": 1,
        "profit": 0.5,
        "recommended_stake": 2.5,
        "ev_percent": 12.0,
    }


def test_holdout_splits_10_into_7_and_3():
    """10 oportunidades cronológicas → treino=7 (antigas), teste=3 (recentes)."""
    rows = [_opp(i + 1) for i in range(10)]
    train, test = compute_holdout(rows)
    assert train["n"] == 7
    assert test["n"] == 3


def test_holdout_orders_by_match_date():
    """Split respeita a ordem cronológica (e não a ordem de inserção)."""
    rows = [_opp(i + 1) for i in range(10)]
    rows.reverse()  # desordena: 10..1
    train, test = compute_holdout(rows)
    assert train["n"] == 7
    assert test["n"] == 3


def test_metrics_baseado_em_resolved_only():
    """Apenas apostas RESOLVIDAS entram no ROI/hit rate."""
    rows = [_opp(i + 1) for i in range(5)]
    rows.append({
        "match_id": 99,
        "match_date": "2025-06-01T12:00:00+00:00",
        "status": "PENDING",
        "result_won": None,
        "profit": None,
        "recommended_stake": 2.5,
        "ev_percent": 50.0,
    })
    metrics = compute_metrics(rows)
    assert metrics["n"] == 5  # PENDING fora da contagem
    assert metrics["hit_rate"] == pytest.approx(100.0)
    assert metrics["roi"] == pytest.approx(0.5 * 5 / (2.5 * 5) * 100.0)


def test_possible_overfitting_detected():
    """ROI_teste < ROI_treino × 0.5 → overfitting (5.0 < 10.0 × 0.5)."""
    assert possible_overfitting({"roi": 10.0, "n": 50}, {"roi": 4.9, "n": 20}) is True


def test_possible_overfitting_ok():
    assert possible_overfitting({"roi": 10.0, "n": 50}, {"roi": 5.1, "n": 20}) is False


def test_possible_overfitting_needs_both_sets():
    assert possible_overfitting({"roi": 10.0, "n": 0}, {"roi": 1.0, "n": 10}) is False
    assert possible_overfitting({"roi": 10.0, "n": 10}, {"roi": 1.0, "n": 0}) is False


def test_minimum_sample_size_known_value():
    """ROI 5% com desvio 20% → n≈125.6 → 126 (fórmula: (2.8×20/5)²)."""
    assert minimum_sample_size(5.0, 20.0) == 126


def test_minimum_sample_size_roi_10():
    """ROI 10% com desvio 20% → n = (2.8016×20/10)² ≈ 31.4 → 32."""
    assert minimum_sample_size(10.0, 20.0) == 32


def test_minimum_sample_size_invalid_inputs():
    assert minimum_sample_size(0.0, 20.0) == math.inf
    assert minimum_sample_size(-5.0, 20.0) == math.inf
    assert minimum_sample_size(5.0, 0.0) == math.inf


def test_roi_std_dev_zero_with_few_bets():
    rows = [_opp(i + 1) for i in range(1)]  # 1 aposta → sem dev padrão amostral
    assert roi_std_dev(rows) == 0.0


def test_run_backtest_prints_holdout_block(capsys, tmp_path, monkeypatch):
    """Integração leve: run_backtest imprime seções HOLDOUT e sample size."""
    from radar_ev import backtester as bt
    from radar_ev.backtester import run_backtest

    db_path = str(tmp_path / "hist.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE opportunities (id INTEGER PRIMARY KEY, match_id INTEGER, "
        "status TEXT, result_won BOOLEAN, profit REAL, recommended_stake REAL, "
        "offered_odd REAL, ev_percent REAL, match_date TIMESTAMP)"
    )
    for i in range(10):
        conn.execute(
            "INSERT INTO opportunities (id, match_id, status, result_won, profit, "
            "recommended_stake, offered_odd, ev_percent, match_date) VALUES (?, ?, "
            "'RESOLVED', 1, 0.5, 2.5, 2.0, 12.0, ?)",
            (i + 1, i + 1, f"2025-01-{i + 1:02d}T12:00:00+00:00"),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(bt, "turso_configured", lambda: False)
    run_backtest(db_path)

    out = capsys.readouterr().out
    assert "=== HOLDOUT ===" in out
    assert "Treino: ROI=" in out
    assert "Teste:  ROI=" in out
    assert "Veredito:" in out
    assert "Sample atual: 10" in out
    assert "Sample mínimo" in out


def test_run_backtest_empty_db(tmp_path, capsys, monkeypatch):
    """Sem dados → mensagem amigável, sem crash."""
    from radar_ev import backtester as bt
    from radar_ev.backtester import run_backtest

    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE opportunities (id INTEGER PRIMARY KEY, match_id INTEGER, "
        "status TEXT, result_won BOOLEAN, profit REAL, recommended_stake REAL, "
        "ev_percent REAL, match_date TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(bt, "turso_configured", lambda: False)
    run_backtest(db_path)
    out = capsys.readouterr().out
    assert "Nenhum dado histórico" in out
