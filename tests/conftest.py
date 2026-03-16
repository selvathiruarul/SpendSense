"""
Pytest configuration: patch the database engine BEFORE any backend module
imports backend.main, so all tests share one in-memory SQLite database.

StaticPool is required for in-memory SQLite with FastAPI TestClient
(TestClient runs the ASGI app in a background thread; without StaticPool
each thread would get a different empty database).
"""
import os

# Must be set before backend.database is first imported
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.database as _db

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_db.engine = _engine
_db.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
