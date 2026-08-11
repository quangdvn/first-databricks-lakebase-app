"""
Lakebase (Databricks-managed Postgres) connection helper.

Connects using a single LAKEBASE_URL (a standard Postgres connection URL,
e.g. postgresql://role:password@host:5432/databricks_postgres?sslmode=require)
pointing at a native Postgres role with a static, non-expiring password.
This keeps setup to a single secret instead of five separate env vars.
"""

import base64
import os
from contextlib import contextmanager

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor

_w = None

_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")


def _get_workspace_client() -> WorkspaceClient:
    """Lazy-load the WorkspaceClient."""
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w


def _lakebase_url() -> str:
    """Fetch and decode the Lakebase connection URL from the Databricks secret scope."""
    w = _get_workspace_client()
    secret = w.secrets.get_secret(scope=_SCOPE, key=_KEY)
    return base64.b64decode(secret.value).decode("utf-8")


@contextmanager
def lakebase_conn():
    """
    Context manager providing a raw psycopg2 connection to Lakebase.
    Example:
        with lakebase_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM massive_records LIMIT 10")
                rows = cur.fetchall()
    """
    conn = psycopg2.connect(_lakebase_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def lakebase_cursor(cursor_factory=RealDictCursor):
    """
    Context manager providing a psycopg2 cursor (RealDictCursor by default).
    Example:
        with lakebase_cursor() as cur:
            cur.execute("SELECT ticker, price FROM massive_records")
            for row in cur:
                print(row["ticker"], row["price"])
    """
    with lakebase_conn() as conn:
        cursor = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cursor
        finally:
            cursor.close()
