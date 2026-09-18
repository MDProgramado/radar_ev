"""
database.py — Data Lake local para registro histórico.

Salva todas as oportunidades (+EV) encontradas pelo sistema para
permitir análises de backtesting e validação de lucratividade.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
                    vig_removed BOOLEAN,
                    vig_divergente BOOLEAN,
                    odd_captured_at TIMESTAMP,
                    pinnacle_reference_odd REAL
                )
            """)
            # Migração para bancos existentes que ainda não têm a coluna
            cursor.execute("PRAGMA table_info(opportunities)")
            columns = {row[1] for row in cursor.fetchall()}
            if "vig_removed" not in columns:
                cursor.execute(
                    "ALTER TABLE opportunities ADD COLUMN vig_removed BOOLEAN"
                )
            if "vig_divergente" not in columns:
                cursor.execute(
                    "ALTER TABLE opportunities ADD COLUMN vig_divergente BOOLEAN"
                )
            if "odd_captured_at" not in columns:
                cursor.execute(
                    "ALTER TABLE opportunities ADD COLUMN odd_captured_at TIMESTAMP"
                )
            if "pinnacle_reference_odd" not in columns:
                cursor.execute(
                    "ALTER TABLE opportunities ADD COLUMN pinnacle_reference_odd REAL"
                )
            # Timeline de odds: permite reconstruir o snapshot da odd capturada
            # (CLV) — opening/pre_match/closing — e comparar Betano vs Pinnacle.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS odds_timeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opportunity_id INTEGER,
                    match_id INTEGER,
                    market TEXT,
                    source TEXT,
                    odd REAL,
                    captured_at TIMESTAMP,
                    phase TEXT
                )
            """)
            # Placares reais: matéria-prima para treinar o modelo Dixon-Coles
            # (que modela a distribuição conjunta dos gols marcados).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS match_results (
                    match_id INTEGER PRIMARY KEY,
                    home_team TEXT,
                    away_team TEXT,
                    home_score INTEGER,
                    away_score INTEGER,
                    league TEXT,
                    season INTEGER,
                    resolved_at TIMESTAMP
                )
            """)
            conn.commit()

    def save_opportunity(self, opp: Opportunity, pinnacle_reference_odd: Optional[float] = None) -> None:
        """Salva uma oportunidade no banco de dados.

        Além da oportunidade em si, registra o snapshot da odd capturada na
        tabela ``odds_timeline`` (fase pre_match), incluindo a odd de referência
        da Pinnacle caso exista para o mesmo mercado/linha. O timestamp da
        captura (``odd_captured_at``) é o momento do INSERT — base para o
        cálculo de CLV posterior (odd capturada vs odd de fechamento).
        """
        captured_at = datetime.now(timezone.utc).isoformat()
        source = getattr(opp, "offered_by", "betano")

        with get_db_connection(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Pega o recommended_stake_percent de forma segura (fallback para 0.0)
            stake = getattr(opp, "recommended_stake_percent", 0.0)
            vig_removed = getattr(opp, "vig_removed", None)
            if vig_removed is not None:
                vig_removed = int(bool(vig_removed))
            vig_divergente = getattr(opp, "vig_divergente", None)
            if vig_divergente is not None:
                vig_divergente = int(bool(vig_divergente))
            
            cursor.execute("""
                INSERT INTO opportunities (
                    match_id, home_team, away_team, league, market, 
                    fair_odd, offered_odd, ev_percent, confidence, 
                    recommended_stake, created_at, match_date, vig_removed,
                    vig_divergente, odd_captured_at, pinnacle_reference_odd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                vig_divergente,
                captured_at,
                pinnacle_reference_odd,
            ))
            opp_id = cursor.lastrowid

            # Snapshot da odd capturada (fase pre_match) — base para CLV.
            cursor.execute("""
                INSERT INTO odds_timeline (
                    opportunity_id, match_id, market, source, odd,
                    captured_at, phase
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                opp_id,
                opp.match.id,
                opp.market,
                source,
                opp.offered_odd,
                captured_at,
                "pre_match",
            ))

            # Odd de referência da Pinnacle para o mesmo mercado/linha.
            if pinnacle_reference_odd is not None:
                cursor.execute("""
                    INSERT INTO odds_timeline (
                        opportunity_id, match_id, market, source, odd,
                        captured_at, phase
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    opp_id,
                    opp.match.id,
                    opp.market,
                    "pinnacle",
                    pinnacle_reference_odd,
                    captured_at,
                    "pre_match",
                ))
            conn.commit()

# Instância global
db = Database()
