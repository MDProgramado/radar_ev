"""
database.py — Data Lake local para registro histórico.

Salva todas as oportunidades (+EV) encontradas pelo sistema para
permitir análises de backtesting e validação de lucratividade.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from radar_ev.db import get_db_connection
from radar_ev.models import Opportunity


class Database:
    """Gerenciador do banco de dados histórico SQLite."""

    def __init__(self, db_path: str = "data/history.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Cria as tabelas se não existirem."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id INTEGER,
                    home_team TEXT,
                    away_team TEXT,
                    league TEXT,
                    market TEXT,
                    fair_odd REAL,
                    offered_odd REAL,
                    ev_percent REAL,
                    confidence REAL,
                    recommended_stake REAL,
                    created_at TIMESTAMP,
                    match_date TIMESTAMP,
                    status TEXT DEFAULT 'PENDING',
                    result_won BOOLEAN,
                    profit REAL,
                    vig_removed BOOLEAN
                )
            """)
            # Migração para bancos existentes que ainda não têm a coluna
            cursor.execute("PRAGMA table_info(opportunities)")
            columns = {row[1] for row in cursor.fetchall()}
            if "vig_removed" not in columns:
                cursor.execute(
                    "ALTER TABLE opportunities ADD COLUMN vig_removed BOOLEAN"
                )
            conn.commit()

    def save_opportunity(self, opp: Opportunity):
        """Salva uma oportunidade no banco de dados."""
        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Pega o recommended_stake_percent de forma segura (fallback para 0.0)
            stake = getattr(opp, "recommended_stake_percent", 0.0)
            vig_removed = getattr(opp, "vig_removed", None)
            if vig_removed is not None:
                vig_removed = int(bool(vig_removed))
            
            cursor.execute("""
                INSERT INTO opportunities (
                    match_id, home_team, away_team, league, market, 
                    fair_odd, offered_odd, ev_percent, confidence, 
                    recommended_stake, created_at, match_date, vig_removed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                opp.match.id,
                opp.match.home_team,
                opp.match.away_team,
                opp.match.league,
                opp.market,
                opp.fair_odd,
                opp.offered_odd,
                opp.ev_percent,
                opp.confidence,
                stake,
                opp.created_at.isoformat(),
                opp.match.datetime.isoformat(),
                vig_removed,
            ))
            conn.commit()

# Instância global
db = Database()
