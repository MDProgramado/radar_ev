"""
cache.py — Camada de cache local usando SQLite.

Salva estatísticas históricas de times e outras respostas pesadas da API
para economizar chamadas de rede e não estourar o limite da API-Football.
"""

import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

from radar_ev.db import get_db_connection


class LocalCache:
    """Gerenciador de cache baseado em SQLite."""
    
    def __init__(self, db_path: str = "data/cache.db"):
        # Garante que a pasta existe
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Inicializa as tabelas do banco de dados caso não existam."""
        # Cache sempre em SQLite LOCAL (use_turso=False), mesmo com o Turso
        # configurado: é dado descartável/efêmero (TTL curto) e re-populável.
        # Persistir no Turso gastaria leitura/escrita na nuvem sem ganho real;
        # em ambientes efêmeros (GitHub Actions) o cache nasce e morre a cada
        # execução, o que é aceitável.
        with get_db_connection(self.db_path, use_turso=False) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_cache (
                    key TEXT PRIMARY KEY,
                    data TEXT,
                    expires_at TIMESTAMP
                )
            """)
            conn.commit()

    def _get(self, key: str) -> Optional[dict]:
        """Busca síncrona no banco."""
        # Cache é local por decisão (ver comentário em _init_db).
        with get_db_connection(self.db_path, use_turso=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data, expires_at FROM api_cache WHERE key = ?", (key,))
            row = cursor.fetchone()
            
            if row:
                data, expires_at_str = row
                expires_at = datetime.fromisoformat(expires_at_str)
                
                # Se ainda não expirou, retorna
                if expires_at > datetime.now(timezone.utc):
                    return json.loads(data)
                
                # Se expirou, deleta
                cursor.execute("DELETE FROM api_cache WHERE key = ?", (key,))
                conn.commit()
                
            return None

    def _set(self, key: str, data: dict, ttl_hours: int = 72):
        """Escrita síncrona no banco."""
        # Cache é local por decisão (ver comentário em _init_db).
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        with get_db_connection(self.db_path, use_turso=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO api_cache (key, data, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(data), expires_at.isoformat())
            )
            conn.commit()

    async def get(self, key: str) -> Optional[dict]:
        """Versão assíncrona para leitura."""
        return await asyncio.to_thread(self._get, key)

    async def set(self, key: str, data: dict, ttl_hours: int = 72):
        """Versão assíncrona para gravação."""
        await asyncio.to_thread(self._set, key, data, ttl_hours)

# Instância global
cache = LocalCache()
