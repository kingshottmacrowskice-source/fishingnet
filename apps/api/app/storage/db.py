from sqlalchemy import create_engine, text
from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={'connect_timeout': 5})


def one(conn, sql, **params):
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def many(conn, sql, **params):
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def execute(conn, sql, **params):
    return conn.execute(text(sql), params)
