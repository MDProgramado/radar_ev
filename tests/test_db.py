"""
test_db.py — Testes da fábrica de conexões (Turso ou SQLite local).

Garantem que:
- O Turso só é ativado com as DUAS variáveis de ambiente presentes.
- URLs do Turso (libsql://, wss://, https://) são normalizadas para https://.
- Sem credenciais, o fallback local devolve uma conexão sqlite3 funcional.
- Com credenciais, a conexão usa o protocolo HTTP do Turso
  (POST /v1/execute via urllib stdlib) e expõe a API sqlite3-like
  (TursoConnection / TursoCursor), sem rede (``urllib.request.urlopen`` mockado).
"""

import io
import json
import sqlite3
import urllib.error
import urllib.request

import pytest

from radar_ev import db as db_module


@pytest.fixture(autouse=True)
def _clear_turso_env(monkeypatch):
    """Isola os testes do ambiente real: nenhuma credencial por padrão."""
    monkeypatch.delenv(db_module.TURSO_DATABASE_URL_ENV, raising=False)
    monkeypatch.delenv(db_module.TURSO_AUTH_TOKEN_ENV, raising=False)


@pytest.fixture(autouse=True)
def _clear_select_cache():
    """O cache de SELECTs é global por processo; zera antes/depois de cada teste."""
    db_module._clear_select_cache()
    yield
    db_module._clear_select_cache()


# ---------------------------------------------------------------------------
# Helpers de resposta HTTP fake (sem rede)
# ---------------------------------------------------------------------------
class StubResponse:
    """Resposta mínima de ``urllib.request.urlopen`` (context manager)."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def _execute_response(cols, rows, affected=0, last_insert_rowid=None):
    return {
        "result": {
            "cols": [{"name": name, "decltype": None} for name in cols],
            "rows": rows,
            "affected_row_count": affected,
            "last_insert_rowid": last_insert_rowid,
            "replication_index": None,
            "rows_read": 0,
            "rows_written": 0,
        }
    }


def _hrana(cells):
    """Converte uma tupla de valores Python em células JSON do protocolo Turso."""
    out = []
    for cell in cells:
        if cell is None:
            out.append({"type": "null"})
        elif isinstance(cell, bool):
            out.append({"type": "integer", "value": "1" if cell else "0"})
        elif isinstance(cell, int):
            out.append({"type": "integer", "value": str(cell)})
        elif isinstance(cell, float):
            out.append({"type": "float", "value": cell})
        elif isinstance(cell, bytes):
            import base64

            out.append({"type": "blob", "base64": base64.b64encode(cell).decode("ascii")})
        else:
            out.append({"type": "text", "value": str(cell)})
    return out


def make_urlopener(responder, monkeypatch):
    """Mocka ``urllib.request.urlopen`` e devolve a lista de Requests capturados."""
    requests = []

    def fake_urlopen(req, timeout=None):
        requests.append(req)
        return responder(req)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


class TestTursoDetection:
    def test_not_configured_by_default(self):
        assert db_module.turso_configured() is False

    def test_requires_url_and_token(self, monkeypatch):
        monkeypatch.setenv(db_module.TURSO_DATABASE_URL_ENV, "libsql://x.turso.io")
        assert db_module.turso_configured() is False

        monkeypatch.setenv(db_module.TURSO_AUTH_TOKEN_ENV, "token")
        assert db_module.turso_configured() is True

    def test_requires_token_and_url(self, monkeypatch):
        monkeypatch.setenv(db_module.TURSO_AUTH_TOKEN_ENV, "token")
        assert db_module.turso_configured() is False

        monkeypatch.setenv(db_module.TURSO_DATABASE_URL_ENV, "libsql://x.turso.io")
        assert db_module.turso_configured() is True


class TestNormalizeTursoUrl:
    def test_https_is_kept(self):
        assert (
            db_module.to_https_turso_url("https://radar.turso.io")
            == "https://radar.turso.io"
        )

    def test_libsql_is_converted_to_https(self):
        assert (
            db_module.to_https_turso_url("libsql://radar.turso.io")
            == "https://radar.turso.io"
        )

    def test_ws_schemes_are_converted_to_https(self):
        assert (
            db_module.to_https_turso_url("wss://radar.turso.io")
            == "https://radar.turso.io"
        )
        assert (
            db_module.to_https_turso_url("ws://radar.turso.io")
            == "https://radar.turso.io"
        )

    def test_missing_scheme_gets_https(self):
        assert (
            db_module.to_https_turso_url("radar.turso.io") == "https://radar.turso.io"
        )


class TestLocalFallback:
    def test_returns_sqlite_connection_with_row_factory(self, tmp_path):
        path = str(tmp_path / "hist.db")
        conn = db_module.get_db_connection(path)
        try:
            assert isinstance(conn, sqlite3.Connection)
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO t (name) VALUES (?)", ("radar",))
            conn.commit()
            row = conn.execute("SELECT name FROM t WHERE id = 1").fetchone()
            assert row["name"] == "radar"
        finally:
            conn.close()

    def test_creates_missing_parent_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "hist.db")
        conn = db_module.get_db_connection(path)
        conn.close()
        assert (tmp_path / "nested" / "hist.db").exists()

    def test_use_turso_false_forces_local_even_when_configured(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(db_module.TURSO_DATABASE_URL_ENV, "https://radar.turso.io")
        monkeypatch.setenv(db_module.TURSO_AUTH_TOKEN_ENV, "secret-token")

        path = str(tmp_path / "cache.db")
        conn = db_module.get_db_connection(path, use_turso=False)
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()


class TestGetDbConnectionTurso:
    def test_builds_http_connection_when_configured(self, monkeypatch):
        """get_db_connection monta TursoConnection com URL https e POST /v1/execute."""
        monkeypatch.setenv(
            db_module.TURSO_DATABASE_URL_ENV, "libsql://radar.turso.io"
        )
        monkeypatch.setenv(db_module.TURSO_AUTH_TOKEN_ENV, "secret-token")

        requests = make_urlopener(
            lambda req: StubResponse(
                json.dumps(_execute_response(["n"], [[_hrana((1,))[0]]])).encode()
            ),
            monkeypatch,
        )

        conn = db_module.get_db_connection("data/history.db")
        assert isinstance(conn, db_module.TursoConnection)
        with conn:
            row = conn.execute("SELECT 1").fetchone()
            assert row["n"] == 1

        req = requests[0]
        assert req.full_url == "https://radar.turso.io/v1/execute"
        assert req.method == "POST"
        assert req.headers["Authorization"] == "Bearer secret-token"
        assert json.loads(req.data) == {"stmt": {"sql": "SELECT 1"}}


# ---------------------------------------------------------------------------
# Conversão de parâmetros
# ---------------------------------------------------------------------------
class TestArgsConversion:
    def test_positional_params(self):
        args = db_module._make_args((7, 1.5, "x", None, b"\x00\x01", True))
        assert args == [
            {"type": "integer", "value": "7"},
            {"type": "float", "value": 1.5},
            {"type": "text", "value": "x"},
            {"type": "null"},
            {"type": "blob", "base64": "AAE="},
            {"type": "integer", "value": "1"},
        ]

    def test_named_params(self):
        args = db_module._make_args({"a": 1, "b": "y"})
        assert args == [
            {"name": "a", "value": {"type": "integer", "value": "1"}},
            {"name": "b", "value": {"type": "text", "value": "y"}},
        ]

    def test_none_means_no_args(self):
        assert db_module._make_args(None) == []

    def test_decode_value_types(self):
        assert db_module._decode_value({"type": "null"}) is None
        assert db_module._decode_value({"type": "integer", "value": "42"}) == 42
        assert db_module._decode_value({"type": "float", "value": 3.14}) == 3.14
        assert db_module._decode_value({"type": "text", "value": "oi"}) == "oi"
        assert db_module._decode_value({"type": "blob", "base64": "AAE="}) == b"\x00\x01"


# ---------------------------------------------------------------------------
# TursoConnection via HTTP fake (urllib)
# ---------------------------------------------------------------------------
class TestTursoConnectionHttp:
    def make_conn(self, responder, monkeypatch, **kwargs):
        make_urlopener(responder, monkeypatch)
        return db_module.TursoConnection("https://radar.turso.io", "secret-token", **kwargs)

    @staticmethod
    def select_response():
        return _execute_response(
            ("a", "b"),
            [
                [_hrana((1, "x"))[0], _hrana((1, "x"))[1]],
                [_hrana((2, "y"))[0], _hrana((2, "y"))[1]],
            ],
        )

    def test_select_rows_by_index_and_key(self, monkeypatch):
        conn = self.make_conn(lambda req: StubResponse(json.dumps(self.select_response()).encode()), monkeypatch)
        row = conn.execute("SELECT a, b FROM t").fetchone()
        assert row[0] == 1
        assert row["a"] == 1
        assert row["b"] == "x"
        assert row.keys() == ("a", "b")

    def test_fetchall_fetchmany_description(self, monkeypatch):
        conn = self.make_conn(lambda req: StubResponse(json.dumps(self.select_response()).encode()), monkeypatch)
        cursor = conn.execute("SELECT a, b FROM t")
        assert [r[0] for r in cursor.fetchmany(1)] == [1]
        assert [r[0] for r in cursor.fetchall()] == [2]
        assert cursor.fetchone() is None
        assert cursor.description[0][0] == "a"
        assert cursor.rowcount == -1

    def test_rowcount_and_lastrowid_for_dml(self, monkeypatch):
        conn = self.make_conn(
            lambda req: StubResponse(
                json.dumps(_execute_response([], [], affected=2, last_insert_rowid="7")).encode()
            ),
            monkeypatch,
        )
        cursor = conn.execute("INSERT INTO t (a, b) VALUES (?, ?)", (1, "x"))
        assert cursor.rowcount == 2
        assert cursor.lastrowid == 7

    def test_sends_params_in_request_payload(self, monkeypatch):
        requests = make_urlopener(
            lambda req: StubResponse(json.dumps(_execute_response(["ok"], [[_hrana((1,))[0]]])).encode()),
            monkeypatch,
        )
        conn = db_module.TursoConnection("https://radar.turso.io", "t")
        conn.execute("SELECT V FROM t WHERE a = ? AND b = ?", (7, "foo")).fetchall()
        payload = json.loads(requests[0].data)
        assert payload["stmt"]["args"] == [
            {"type": "integer", "value": "7"},
            {"type": "text", "value": "foo"},
        ]

    def test_executemany_runs_each_param_set(self, monkeypatch):
        requests = make_urlopener(
            lambda req: StubResponse(json.dumps(_execute_response([], [], affected=2)).encode()),
            monkeypatch,
        )
        conn = db_module.TursoConnection("https://radar.turso.io", "t")
        conn.executemany("INSERT INTO t VALUES (?, ?)", [(1, 2), (3, 4)])
        assert len(requests) == 2
        assert json.loads(requests[0].data)["stmt"]["args"] == [
            {"type": "integer", "value": "1"},
            {"type": "integer", "value": "2"},
]

    def test_row_factory_applied(self, monkeypatch):
        conn = self.make_conn(lambda req: StubResponse(json.dumps(self.select_response()).encode()), monkeypatch)
        conn.row_factory = lambda cursor, values: tuple(values)
        assert conn.execute("SELECT a, b FROM t").fetchone() == (1, "x")

    def test_http_error_4xx_raises_operational_error(self, monkeypatch):
        calls = {"n": 0}

        def responder(req):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", None,
                io.BytesIO(b'{"error":"JWT error: InvalidToken"}'),
            )

        conn = self.make_conn(responder, monkeypatch)
        with pytest.raises(sqlite3.OperationalError, match="HTTP 401") as excinfo:
            conn.execute("SELECT 1").fetchall()
        assert "JWT error" in str(excinfo.value)
        assert calls["n"] == 1  # 4xx não é transitório → sem retry

    def test_timeout_raises_operational_error(self, monkeypatch):
        calls = {"n": 0}

        def responder(req):
            calls["n"] += 1
            raise TimeoutError("timed out")

        conn = self.make_conn(responder, monkeypatch)
        with pytest.raises(sqlite3.OperationalError, match="timed out"):
            conn.execute("SELECT 1").fetchall()
        assert calls["n"] == 2  # timeout é de transporte → SELECT tem 1 retry

    def test_retries_select_once_on_transient_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        def responder(req):
            calls["n"] += 1
            if calls["n"] < 2:
                raise TimeoutError("timed out")
            return StubResponse(json.dumps(self.select_response()).encode())

        conn = self.make_conn(responder, monkeypatch)
        assert conn.execute("SELECT a, b FROM t").fetchone()["a"] == 1
        assert calls["n"] == 2

    def test_retries_select_once_on_http_500_then_succeeds(self, monkeypatch):
        calls = {"n": 0}

        def responder(req):
            calls["n"] += 1
            if calls["n"] < 2:
                raise urllib.error.HTTPError(
                    req.full_url, 500, "Internal Server Error", None, io.BytesIO(b"boom")
                )
            return StubResponse(json.dumps(self.select_response()).encode())

        conn = self.make_conn(responder, monkeypatch)
        assert conn.execute("SELECT a, b FROM t").fetchone()["a"] == 1
        assert calls["n"] == 2

    def test_logical_sql_error_is_not_retried(self, monkeypatch):
        calls = {"n": 0}

        def responder(req):
            calls["n"] += 1
            return StubResponse(b'{"message":"SQLite error: no such table: t","code":"SQLITE_UNKNOWN"}')

        conn = self.make_conn(responder, monkeypatch)
        with pytest.raises(sqlite3.OperationalError, match="SQLITE_UNKNOWN"):
            conn.execute("SELECT * FROM t").fetchall()
        assert calls["n"] == 1  # erro lógico do SQLite → sem retry (round-trip único)

    def test_malformed_response_raises_database_error(self, monkeypatch):
        conn = self.make_conn(lambda req: StubResponse(b"<html>oops</html>"), monkeypatch)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT 1").fetchall()

    def test_retries_disabled(self, monkeypatch):
        calls = {"n": 0}

        def responder(req):
            calls["n"] += 1
            raise TimeoutError("boom")

        conn = self.make_conn(responder, monkeypatch, retries=0)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT 1").fetchall()
        assert calls["n"] == 1

    def test_identical_selects_are_cached(self, monkeypatch):
        requests = make_urlopener(
            lambda req: StubResponse(json.dumps(self.select_response()).encode()),
            monkeypatch,
        )
        conn = db_module.TursoConnection("https://radar.turso.io", "t")
        with conn:
            assert len(conn.execute("SELECT a, b FROM t").fetchall()) == 2
            assert len(conn.execute("SELECT a, b FROM t").fetchall()) == 2
        assert len(requests) == 1

    def test_cache_is_cleared_by_dml(self, monkeypatch):
        requests = make_urlopener(
            lambda req: StubResponse(json.dumps(_execute_response([], [], affected=1)).encode()),
            monkeypatch,
        )
        conn = db_module.TursoConnection("https://radar.turso.io", "t", use_cache=True)
        conn.execute("SELECT a, b FROM t").fetchall()
        conn.execute("SELECT a, b FROM t").fetchall()
        assert len(requests) == 1
        conn.execute("INSERT INTO t VALUES (1)").fetchall()
        conn.execute("SELECT a, b FROM t").fetchall()
        assert len(requests) == 3

    def test_cache_can_be_disabled(self, monkeypatch):
        requests = make_urlopener(
            lambda req: StubResponse(json.dumps(self.select_response()).encode()),
            monkeypatch,
        )
        conn = db_module.TursoConnection("https://radar.turso.io", "t", use_cache=False)
        conn.execute("SELECT a, b FROM t").fetchall()
        conn.execute("SELECT a, b FROM t").fetchall()
        assert len(requests) == 2

    def test_context_manager_closes_and_noops_transactions(self, monkeypatch):
        conn = self.make_conn(lambda req: StubResponse(json.dumps(self.select_response()).encode()), monkeypatch)
        with conn:
            conn.commit()
            conn.rollback()
        assert conn.closed is True
        conn.close()
        assert conn.closed is True
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1").fetchall()
