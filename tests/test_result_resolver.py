"""
test_result_resolver.py — Testes do resolutor de resultados ('match_results').

Garante que, ao resolver um jogo, o placar real é extraído de
/fixtures?id=X e persistido em ``match_results`` de forma idempotente
(INSERT OR REPLACE), gerando a matéria-prima para o modelo Dixon-Coles.
"""

import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

from radar_ev.database import Database
from radar_ev.result_resolver import ResultResolver


def _make_db_with_pending(path: str) -> None:
    """Banco com schema completo + 1 oportunidade PENDING já encerrada."""
    Database(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO opportunities "
        "(id, match_id, market, offered_odd, recommended_stake, status, match_date) "
        "VALUES (1, 4242, 'goals_over_2.5', 2.0, 1.0, 'PENDING', ?)",
        ("2020-01-01T00:00:00+00:00",),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_resolve_all_saves_match_result(tmp_path):
    """Mock de resposta da API (FT, 4x2) → placar persistido em match_results."""
    db_path = str(tmp_path / "hist.db")
    _make_db_with_pending(db_path)

    fixture_data = {
        "fixture": {"id": 4242, "status": {"short": "FT"}},
        "goals": {"home": 3, "away": 1},
        "teams": {
            "home": {"name": "Palmeiras"},
            "away": {"name": "Corinthians"},
        },
        "league": {"name": "Serie A", "season": 2026},
        "statistics": [],
    }

    resolver = ResultResolver()
    resolver.db_path = db_path
    resolver.telegram = Mock()
    resolver._fetch_fixture_results = AsyncMock(return_value=fixture_data)
    resolver._send_daily_report = AsyncMock()

    await resolver.resolve_all()

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT * FROM match_results WHERE match_id = 4242").fetchone()
    conn.close()

    assert row is not None
    assert row[1] == "Palmeiras"        # home_team
    assert row[2] == "Corinthians"      # away_team
    assert row[3] == 3                  # home_score
    assert row[4] == 1                  # away_score
    assert row[5] == "Serie A"          # league
    assert row[6] == 2026               # season
    assert row[7] is not None           # resolved_at


@pytest.mark.asyncio
async def test_resolve_all_is_idempotent(tmp_path):
    """Re-resolver o mesmo jogo → 1 única linha (INSERT OR REPLACE)."""
    db_path = str(tmp_path / "hist.db")
    _make_db_with_pending(db_path)

    fixture_data = {
        "fixture": {"id": 4242, "status": {"short": "FT"}},
        "goals": {"home": 3, "away": 1},
        "teams": {
            "home": {"name": "Palmeiras"},
            "away": {"name": "Corinthians"},
        },
        "league": {"name": "Serie A", "season": 2026},
        "statistics": [],
    }

    resolver = ResultResolver()
    resolver.db_path = db_path
    resolver.telegram = Mock()
    resolver._fetch_fixture_results = AsyncMock(return_value=fixture_data)
    resolver._send_daily_report = AsyncMock()

    await resolver.resolve_all()
    # Segunda passada: a oportunidade agora está RESOLVED, mas o match_results
    # também pode receber um segundo update (placecar alterado) sem duplicar.
    resolver._update_opportunity(1, True, 1.0)
    await resolver.resolve_all()

    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM match_results WHERE match_id = 4242"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_save_match_result_skips_not_finished(tmp_path):
    """Jogo não terminado (1T/HT) não gera registro de placar."""
    db_path = str(tmp_path / "hist.db")
    Database(db_path)

    resolver = ResultResolver()
    resolver.db_path = db_path
    resolver._save_match_result(
        {"fixture": {"id": 999, "status": {"short": "HT"}}, "goals": {"home": 0, "away": 0}}
    )

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
    conn.close()
    assert count == 0
