"""
test_db.py — Testes da fábrica de conexões (Turso ou SQLite local).

Garantem que:
- O Turso só é ativado com as DUAS variáveis de ambiente presentes.
- URLs https:// do Turso são normalizadas para libsql://.
- Sem credenciais, o fallback local devolve uma conexão sqlite3 funcional.
- Com credenciais, a conexão delega ao dbapi2 do libsql-client.
"""

import sqlite3

import pytest

from radar_ev import db as db_module


@pytest.fixture(autouse=True)
def _clear_turso_env(monkeypatch):
    """Isola os testes do ambiente real: nenhuma credencial por padrão."""
    monkeypatch.delenv(db_module.TURSO_DATABASE_URL_ENV, raising=False)
    monkeypatch.delenv(db_module.TURSO_AUTH_TOKEN_ENV, raising=False)


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
    def test_https_is_converted_to_libsql(self):
        assert (
            db_module.normalize_turso_url("https://radar.turso.io")
            == "libsql://radar.turso.io"
        )

    def test_libsql_is_kept(self):
        assert (
            db_module.normalize_turso_url("libsql://radar.turso.io")
            == "libsql://radar.turso.io"
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
        monkeypatch.setenv(
            db_module.TURSO_DATABASE_URL_ENV, "https://radar.turso.io"
        )
        monkeypatch.setenv(db_module.TURSO_AUTH_TOKEN_ENV, "secret-token")

        path = str(tmp_path / "cache.db")
        conn = db_module.get_db_connection(path, use_turso=False)
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()


class TestTursoConnection:
    def test_delegates_to_libsql_when_configured(self, monkeypatch):
        captured = {}

        class FakeConnection:
            row_factory = None

        def fake_connect(url, auth_token=None, **kwargs):
            captured["url"] = url
            captured["auth_token"] = auth_token
            return FakeConnection()

        monkeypatch.setenv(
            db_module.TURSO_DATABASE_URL_ENV, "https://radar.turso.io"
        )
        monkeypatch.setenv(db_module.TURSO_AUTH_TOKEN_ENV, "secret-token")
        monkeypatch.setattr(
            "libsql_client.dbapi2.connect", fake_connect, raising=False
        )

        conn = db_module.get_db_connection("data/history.db")

        assert captured["url"] == "libsql://radar.turso.io"
        assert captured["auth_token"] == "secret-token"
        assert isinstance(conn, FakeConnection)
        assert conn.row_factory is not None
