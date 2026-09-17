"""
db.py — Fábrica de conexões de banco de dados (Turso ou SQLite local).

O Radar +EV fala SQLite puro. Em ambientes efêmeros (ex.: GitHub Actions) o
arquivo não pode viver no disco da máquina, então o banco é hospedado no
Turso — SQLite gerenciado na nuvem, acessível via protocolo Hrana. Em
desenvolvimento local, quando o Turso não está configurado, caímos
automaticamente para um arquivo SQLite em disco.

As credenciais são lidas exclusivamente do ambiente do processo (nunca
hardcoded no código):

- ``TURSO_DATABASE_URL``: ex.: ``libsql://radar-<org>.turso.io``
- ``TURSO_AUTH_TOKEN``: token de acesso ao banco

Ambas são obrigatórias para ativar o Turso. Se qualquer uma estiver ausente,
o fallback local em ``data/*.db`` é usado.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

TURSO_DATABASE_URL_ENV = "TURSO_DATABASE_URL"
TURSO_AUTH_TOKEN_ENV = "TURSO_AUTH_TOKEN"


def turso_configured() -> bool:
    """Indica se as credenciais do Turso estão presentes no ambiente."""
    return bool(
        os.environ.get(TURSO_DATABASE_URL_ENV)
        and os.environ.get(TURSO_AUTH_TOKEN_ENV)
    )


def normalize_turso_url(url: str) -> str:
    """Converte URLs ``https://`` do Turso para o esquema ``libsql://``.

    O ``dbapi2`` do libsql-client fala Hrana via WebSocket e aceita apenas
    ``libsql://``, ``ws://`` e ``wss://``. O painel do Turso também expõe a
    URL no formato ``https://<db>.turso.io``; convertemos para o alias
    ``libsql://`` para suportar os dois formatos.
    """
    if url.startswith("https://"):
        return "libsql://" + url[len("https://") :]
    return url


def get_db_connection(
    db_path: str = "data/history.db", *, use_turso: bool = True
) -> Any:
    """Retorna uma conexão com o banco: Turso (nuvem) ou SQLite local.

    A decisão é feita em tempo de execução, a cada chamada:

    - Se ``use_turso`` for ``True`` e ``TURSO_DATABASE_URL`` /
      ``TURSO_AUTH_TOKEN`` estiverem definidas, conecta ao Turso usando
      ``libsql_client.dbapi2`` — um drop-in compatível com a API do ``sqlite3``.
    - Caso contrário, faz fallback para o arquivo SQLite local em
      ``db_path`` (ambiente de desenvolvimento).

    Args:
        db_path: caminho do SQLite local, usado no fallback.
        use_turso: quando ``False``, ignora as credenciais do Turso e força o
            SQLite local — útil para dados descartáveis/efêmeros (ex.: cache).

    A conexão retornada já vem com ``row_factory`` configurado (``sqlite3.Row``
    no modo local e o ``Row`` equivalente do libsql no modo Turso), de modo
    que ``row["coluna"]`` funciona nos dois modos. O chamador é responsável
    por fechar a conexão (``with`` ou ``conn.close()``).
    """
    if use_turso and turso_configured():
        from libsql_client import dbapi2 as libsql_dbapi2

        conn = libsql_dbapi2.connect(
            normalize_turso_url(os.environ[TURSO_DATABASE_URL_ENV]),
            auth_token=os.environ[TURSO_AUTH_TOKEN_ENV],
        )
        conn.row_factory = libsql_dbapi2.Row
        return conn

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn
