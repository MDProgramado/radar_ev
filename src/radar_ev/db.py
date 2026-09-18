"""
db.py — Fábrica de conexões de banco de dados (Turso ou SQLite local).

O Radar +EV fala SQLite puro. Em ambientes efêmeros (ex.: GitHub Actions) o
arquivo não pode viver no disco da máquina, então o banco é hospedado no
Turso — SQLite gerenciado na nuvem, acessível via protocolo HTTP. Em
desenvolvimento local, quando o Turso não está configurado, caímos
automaticamente para um arquivo SQLite em disco.

Transporte para o Turso:
    Endpoints geridos do Turso em regiões AWS (ex.: ``aws-ap-south-1``) falam
    apenas o protocolo HTTP (Hrana over HTTPS); NÃO aceitam WebSocket/Hrana.
    Por isso ``libsql://``/``wss://`` são convertidos para ``https://`` na
    entrada. A comunicação usa **apenas a stdlib** (``urllib.request`` /
    ``json``) contra o endpoint ``POST {base}/v1/execute`` — sem dependências
    externas. O antigo ``libsql-client`` (0.3.1, descontinuado) foi removido
    por ser flaky (KeyError intermitente em reads) e só falar WS/Hrana.

    Formato do request:  {"stmt": {"sql": "...", "args": [...]}}
    Formato da resposta: {"result": {"cols": [...], "rows": [...],
                                     "affected_row_count": N,
                                     "last_insert_rowid": M}}
    Erros de SQL voltam com HTTP 200 no shape {"message", "code"}; erros de
    transporte/auth voltam como 4xx/5xx com {"error"}. Transações
    (/v1/transaction) NÃO são suportadas nos endpoints AWS — cada statement é
    autocommit.

As credenciais são lidas exclusivamente do ambiente do processo (nunca
hardcoded no código):

- ``TURSO_DATABASE_URL``: ex.: ``libsql://radar-<org>.turso.io``
- ``TURSO_AUTH_TOKEN``: token de acesso ao banco

Ambas são obrigatórias para ativar o Turso. Se qualquer uma estiver ausente,
o fallback local em ``data/*.db`` é usado.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

TURSO_DATABASE_URL_ENV = "TURSO_DATABASE_URL"
TURSO_AUTH_TOKEN_ENV = "TURSO_AUTH_TOKEN"

_DEFAULT_TIMEOUT = 30.0
_MAX_CACHE_ENTRIES = 128

# Cache em memória de SELECTs idênticos dentro do mesmo processo/run. O Turso
# AWS não roda no mesmo processo em paralelo (uma coleta por vez), então um
# SELECT cacheado não pode ficar obsoleto — e qualquer DML/DDL limpa o cache.
_SELECT_CACHE: dict[str, _Result] = {}


def _clear_select_cache() -> None:
    _SELECT_CACHE.clear()


def turso_configured() -> bool:
    """Indica se as credenciais do Turso estão presentes no ambiente."""
    return bool(
        os.environ.get(TURSO_DATABASE_URL_ENV)
        and os.environ.get(TURSO_AUTH_TOKEN_ENV)
    )


def to_https_turso_url(url: str) -> str:
    """Converte URLs do Turso para o esquema ``https://`` (protocolo HTTP).

    Os esquemas ``libsql://``/``ws://``/``wss://`` são WebSocket (Hrana), que
    os endpoints do Turso em regiões AWS não suportam. A conversão acontece na
    entrada da conexão, antes do cliente HTTP (``urllib``).
    """
    if "://" not in url:
        return "https://" + url
    for scheme in ("libsql://", "wss://", "ws://", "https://", "http://"):
        if url.startswith(scheme):
            return "https://" + url[len(scheme) :]
    return url


# ---------------------------------------------------------------------------
# Conversão de valores sqlite3 <-> protocolo HTTP do Turso
# ---------------------------------------------------------------------------
def _to_hrana_value(value: Any) -> dict:
    """Converte um valor do Python para o formato JSON usado no /v1/execute."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, bytes | bytearray):
        return {
            "type": "blob",
            "base64": base64.b64encode(bytes(value)).decode("ascii"),
        }
    return {"type": "text", "value": str(value)}


def _make_args(parameters: Any) -> list[dict]:
    """Converte os parâmetros (sqlite3) no array ``args`` do protocolo Turso."""
    if parameters is None:
        return []
    if isinstance(parameters, Mapping):
        return [
            {"name": str(key), "value": _to_hrana_value(value)}
            for key, value in parameters.items()
        ]
    return [_to_hrana_value(value) for value in parameters]


def _decode_value(value: dict) -> Any:
    """Converte um valor do JSON Turso para o tipo Python equivalente."""
    value_type = value.get("type")
    if value_type is None or value_type == "null":
        return None
    if value_type == "integer":
        return int(value["value"])
    if value_type == "float":
        return float(value["value"])
    if value_type == "blob":
        return base64.b64decode(value.get("base64", ""))
    return value.get("value")


def _is_select(sql: str) -> bool:
    return sql.lstrip().lower().startswith("select")


class _TursoTransientError(sqlite3.OperationalError):
    """Erro transitório de transporte (rede, timeout ou HTTP >= 500).

    É o ÚNICO tipo de erro que justifica reintentar: em SELECTs (idempotentes)
    repetimos uma vez. Erros lógicos do SQLite (resposta HTTP 200 com
    ``{"message", "code"}``) e erros 4xx (ex.: auth) NÃO são transitórios e
    não são repetidos.
    """


def _cache_key(sql: str, args: list[dict]) -> str:
    return json.dumps({"sql": sql, "args": args}, sort_keys=True, separators=(",", ":"))


class TursoRow(Sequence[Any]):
    """Linha acessível por índice e por nome de coluna (como ``sqlite3.Row``)."""

    __slots__ = ("_columns", "_values")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            return self._values[self._columns.index(key)]
        return self._values[key]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def keys(self) -> tuple[str, ...]:
        """Nomes das colunas (compatível com ``sqlite3.Row.keys``)."""
        return self._columns

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TursoRow):
            return self._values == other._values
        if isinstance(other, Sequence):
            return tuple(self) == tuple(other)
        return NotImplemented

    def __repr__(self) -> str:
        return f"<TursoRow {list(self._values)!r}>"


class _Result:
    """Resultado interno de um execute: colunas, linhas e metadados."""

    __slots__ = ("columns", "rows", "rows_affected", "last_insert_rowid")

    def __init__(
        self,
        columns: list[str],
        rows: list[TursoRow],
        rows_affected: int,
        last_insert_rowid: int | None,
    ) -> None:
        self.columns = columns
        self.rows = rows
        self.rows_affected = rows_affected
        self.last_insert_rowid = last_insert_rowid

    @classmethod
    def from_dict(cls, data: dict) -> _Result:
        columns = [col.get("name", "") for col in data.get("cols", [])]
        raw_rows = data.get("rows", [])
        rows = [
            TursoRow(columns, [_decode_value(cell) for cell in row]) for row in raw_rows
        ]
        last_id = data.get("last_insert_rowid")
        return cls(
            columns,
            rows,
            int(data.get("affected_row_count", 0) or 0),
            int(last_id) if last_id is not None else None,
        )


class TursoCursor:
    """Cursor com a API do ``sqlite3`` sobre o cliente HTTP do Turso."""

    def __init__(self, conn: TursoConnection) -> None:
        self._conn = conn
        self._result: _Result | None = None
        self._rows: list[TursoRow] = []
        self._index: int = 0
        self.arraysize: int = 1

    @property
    def connection(self) -> TursoConnection:
        return self._conn

    @property
    def description(self) -> tuple[tuple[str, None, None, None, None, None, None], ...] | None:
        if self._result is None or not self._result.columns:
            return None
        return tuple((col, None, None, None, None, None, None) for col in self._result.columns)

    @property
    def rowcount(self) -> int:
        if self._result is None:
            return -1
        if self._result.columns:
            return -1
        return self._result.rows_affected

    @property
    def lastrowid(self) -> int | None:
        if self._result is None:
            return None
        return self._result.last_insert_rowid

    def execute(self, sql: str, parameters: Any = None) -> TursoCursor:
        result = self._conn._query(sql, _make_args(parameters))
        self._result = result
        self._rows = list(result.rows)
        self._index = 0
        return self

    def executemany(self, sql: str, seq_of_parameters: Any) -> TursoCursor:
        for parameters in seq_of_parameters:
            self.execute(sql, parameters)
        return self

    def _wrap_row(self, row: TursoRow) -> Any:
        factory = self._conn.row_factory
        if factory is None:
            return row
        return factory(self, tuple(row))

    def fetchone(self) -> Any:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return self._wrap_row(row)

    def fetchall(self) -> list:
        rows = [self._wrap_row(row) for row in self._rows[self._index:]]
        self._index = len(self._rows)
        return rows

    def fetchmany(self, size: int | None = None) -> list:
        if size is None:
            size = self.arraysize
        end = self._index + size
        rows = [self._wrap_row(row) for row in self._rows[self._index:end]]
        self._index = end
        return rows


class TursoConnection:
    """Conexão sqlite3-like para o Turso via HTTP puro (stdlib).

    Falas o protocolo ``POST /v1/execute`` do Turso (Hrana v1 over HTTP) com
    ``urllib.request``. O protocolo é autocommit por statement: ``commit`` e
    ``rollback`` são no-ops mantidos apenas para compatibilidade com a API do
    ``sqlite3`` (e.g. ``with conn:``). Diferente do ``sqlite3`` (em que o
    ``with`` só commita), aqui o ``with`` também **fecha** a conexão ao sair.

    As linhas retornadas são ``TursoRow``, acessíveis tanto por índice
    (``row[0]``) quanto por chave (``row["name"]``). Uma ``row_factory``
    customizada (callable ``(cursor, values) -> row``) pode ser atribuída e é
    aplicada ao converter a linha — note que a ``sqlite3.Row`` padrão exige um
    cursor ``sqlite3`` real, então neste modo remoto use a sua própria ou
    deixe ``None`` (padrão: ``TursoRow`` com acesso por índice e por chave).

    SELECTs idênticos (mesma SQL + args) no mesmo run são satisfeitos pelo
    cache em memória (evita round-trips); qualquer DML/DDL limpa o cache.
    Em SELECTs, um erro transitório de TRANSPORTE (rede, timeout ou
    HTTP >= 500) é repetido uma vez antes de propagar; erros lógicos do
    SQLite (HTTP 200 com ``{message, code}``) e erros 4xx não são repetidos.
    """

    def __init__(
        self,
        url: str,
        auth_token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        retries: int = 1,
        use_cache: bool = True,
    ) -> None:
        self._url = url.rstrip("/")
        self._auth_token = auth_token
        self._timeout = timeout
        self._retries = max(0, retries)
        self._use_cache = use_cache
        self.row_factory: Any = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def cursor(self) -> TursoCursor:
        return TursoCursor(self)

    def execute(self, sql: str, parameters: Any = None) -> TursoCursor:
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> TursoCursor:
        return self.cursor().executemany(sql, seq_of_parameters)

    def commit(self) -> None:
        """No-op: o protocolo HTTP do Turso é autocommit por statement."""

    def rollback(self) -> None:
        """No-op: o protocolo HTTP do Turso é autocommit por statement."""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

    def __enter__(self) -> TursoConnection:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    # -- transporte ---------------------------------------------------------
    def _query(self, sql: str, args: list[dict]) -> _Result:
        """Executa um statement, com cache de SELECTs e retry de SELECTs."""
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        select = _is_select(sql)
        key = _cache_key(sql, args)
        if select and self._use_cache and key in _SELECT_CACHE:
            return _SELECT_CACHE[key]

        attempts = self._retries + 1
        result: _Result | None = None
        for attempt in range(attempts):
            try:
                result = self._request(sql, args)
                break
            except _TursoTransientError:
                if not select or attempt >= attempts - 1:
                    raise
                continue

        if result is None:  # pragma: no cover - inatingível (retry sempre retorna ou levanta)
            raise sqlite3.OperationalError("Turso: sem resultado após retries")

        if select:
            if self._use_cache:
                if len(_SELECT_CACHE) >= _MAX_CACHE_ENTRIES:
                    _clear_select_cache()
                _SELECT_CACHE[key] = result
        else:
            _clear_select_cache()
        return result

    def _request(self, sql: str, args: list[dict]) -> _Result:
        payload: dict = {"stmt": {"sql": sql}}
        if args:
            payload["stmt"]["args"] = args
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._url}/v1/execute",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._auth_token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code >= 500:
                raise _TursoTransientError(
                    f"falha de conexão com o Turso ({self._url}): HTTP {exc.code}"
                ) from exc
            raise self._http_error(f"Turso HTTP {exc.code}", raw) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _TursoTransientError(
                f"falha de conexão com o Turso ({self._url}): {exc}"
            ) from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise sqlite3.DatabaseError(f"resposta inválida do Turso: {exc}") from exc

        if "result" not in data:
            message = data.get("message") or data.get("error") or f"resposta inesperada: {data!r}"
            code = data.get("code", "TURSO_ERROR")
            raise sqlite3.OperationalError(f"{code}: {message}")
        return _Result.from_dict(data["result"])

    @staticmethod
    def _http_error(prefix: str, raw: bytes) -> sqlite3.OperationalError:
        try:
            data = json.loads(raw.decode("utf-8"))
            detail = data.get("message") or data.get("error") or raw.decode("utf-8")[:200]
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = raw.decode("utf-8", "replace")[:200]
        return sqlite3.OperationalError(f"{prefix}: {detail}")


def get_db_connection(
    db_path: str = "data/history.db", *, use_turso: bool = True
) -> Any:
    """Retorna uma conexão com o banco: Turso (nuvem) ou SQLite local.

    A decisão é feita em tempo de execução, a cada chamada:

    - Se ``use_turso`` for ``True`` e ``TURSO_DATABASE_URL`` /
      ``TURSO_AUTH_TOKEN`` estiverem definidas, conecta ao Turso usando o
      protocolo HTTP (urllib puro / ``/v1/execute``) através de
      ``TursoConnection`` — um drop-in da API do ``sqlite3``.
    - Caso contrário, faz fallback para o arquivo SQLite local em
      ``db_path`` (ambiente de desenvolvimento).

    Args:
        db_path: caminho do SQLite local, usado no fallback.
        use_turso: quando ``False``, ignora as credenciais do Turso e força o
            SQLite local — útil para dados descartáveis/efêmeros (ex.: cache).

    A conexão retornada já vem com ``row_factory`` configurado (``sqlite3.Row``
    no modo local e ``TursoRow`` no modo Turso, acessíveis por índice e por
    chave). O chamador é responsável por fechar a conexão (``with`` ou
    ``conn.close()``).
    """
    if use_turso and turso_configured():
        return TursoConnection(
            to_https_turso_url(os.environ[TURSO_DATABASE_URL_ENV]),
            os.environ[TURSO_AUTH_TOKEN_ENV],
        )

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn
