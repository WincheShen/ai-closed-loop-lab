"""PostgreSQL connection helpers for strategy_mining schema.

Reads connection info from environment variables (loaded via python-dotenv
in entry-point scripts). All functions return psycopg2 connections with
``search_path`` set to ``strategy_mining,public``.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PgConnection


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("PG_HOST", "192.168.3.73"),
        "port": int(os.environ.get("PG_PORT", "5433")),
        "dbname": os.environ.get("PG_DB", "agent_data"),
        "user": os.environ.get("PG_USER", "admin"),
        "password": os.environ["PG_PASSWORD"],
        "connect_timeout": int(os.environ.get("PG_CONNECT_TIMEOUT", "10")),
    }


def get_connection(autocommit: bool = False) -> PgConnection:
    """Open a new PostgreSQL connection with search_path on strategy_mining."""
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = autocommit
    with conn.cursor() as cur:
        cur.execute("SET search_path TO strategy_mining, public;")
    if not autocommit:
        conn.commit()
    return conn


@contextmanager
def connection_scope(autocommit: bool = False) -> Iterator[PgConnection]:
    """Context manager that opens, yields and closes a connection.

    Commits on clean exit (when not autocommit), rolls back on exception.
    """
    conn = get_connection(autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def ping() -> dict:
    """Sanity check: return server version and current database/user."""
    with connection_scope(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT version(), current_database(), current_user, now();")
        version, db, user, ts = cur.fetchone()
        cur.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'strategy_mining';
            """
        )
        (table_count,) = cur.fetchone()
    return {
        "version": version,
        "database": db,
        "user": user,
        "now": ts,
        "strategy_mining_tables": table_count,
    }
