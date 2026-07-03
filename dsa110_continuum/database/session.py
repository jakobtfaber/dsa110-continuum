# Vendored from dsa110-contimg @ /data/dsa110-contimg/backend/src (H17), 2026-07-03,
# infrastructure/database/session.py, as part of the contimg-import-retirement migration
# (docs/rse/specs/plan-contimg-import-retirement.md, Phase 5).
"""
SQLAlchemy database session management for DSA-110 Continuum Imaging Pipeline.

This module provides:
- Database engine factory with proper SQLite configuration (WAL mode, 30s timeout)
- Session factories for each database
- Scoped sessions for multi-threaded contexts (streaming converter)
- Context managers for safe session handling

Examples
--------
Simple session usage with context manager::

    from dsa110_contimg.infrastructure.database.session import get_session

    with get_session("pipeline") as session:
        images = session.query(Image).filter_by(type="dirty").all()
        session.add(new_image)
        session.commit()

Scoped sessions for multi-threaded contexts::

    from dsa110_contimg.infrastructure.database.session import get_scoped_session

    Session = get_scoped_session("pipeline")
    session = Session()
    try:
        # do work
        session.commit()
    finally:
        Session.remove()

Direct engine access for migrations::

    from dsa110_contimg.infrastructure.database.session import get_engine

    engine = get_engine("pipeline")
    Base.metadata.create_all(engine)

Notes
-----
The unified pipeline database is used for all domain data:

- PIPELINE_DB -> /data/dsa110-contimg/state/db/pipeline.sqlite3

Separate utility databases:

- docsearch, embedding_cache remain independent
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from threading import RLock
from typing import TYPE_CHECKING, Literal

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from dsa110_continuum.utils import get_env_path

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

# =============================================================================
# Database Path Configuration
# =============================================================================

# Default database paths - use CONTIMG_BASE_DIR env var if set
_CONTIMG_BASE = get_env_path("CONTIMG_BASE_DIR", default="/data/dsa110-contimg")
_PIPELINE_DB_ENV = os.environ.get("PIPELINE_DB")
DEFAULT_PIPELINE_DB = _PIPELINE_DB_ENV if _PIPELINE_DB_ENV else str(_CONTIMG_BASE / "state/db/pipeline.sqlite3")


# Default database paths - unified pipeline DB for domain data
_STATE_DIR = get_env_path("CONTIMG_STATE_DIR", default="/data/dsa110-contimg/state")
DEFAULT_DB_PATHS = {
    # Unified pipeline database (contains all domain tables)
    "pipeline": DEFAULT_PIPELINE_DB,
    # Separate utility databases
    "docsearch": str(_STATE_DIR / "db/docsearch.sqlite3"),
    "embedding_cache": str(_STATE_DIR / "db/embedding_cache.sqlite3"),
}

# Alias for Alembic migrations
DATABASE_PATHS = DEFAULT_DB_PATHS

# Environment variable names for database paths
# All domain databases use PIPELINE_DB
DB_ENV_VARS = {
    "pipeline": "PIPELINE_DB",
    "docsearch": "PIPELINE_DOCSEARCH_DB",
    "embedding_cache": "PIPELINE_EMBEDDING_CACHE_DB",
}

LEGACY_ENV_VARS: dict[str, str] = {}

# Database name type for type hints
DatabaseName = Literal[
    "pipeline",
    "docsearch",
    "embedding_cache",
]

# =============================================================================
# Engine and Session Caching
# =============================================================================

# Global caches for engines and session factories
_engines: dict[str, Engine] = {}
_session_factories: dict[str, sessionmaker] = {}
_scoped_sessions: dict[str, scoped_session] = {}
_lock = RLock()  # Use RLock for reentrant locking (get_session_factory calls get_engine)

# SQLite connection settings
SQLITE_TIMEOUT = 30  # seconds
SQLITE_CHECK_SAME_THREAD = False  # Allow multi-threaded access


def get_db_path(db_name: DatabaseName) -> str:
    """Get the database file path for a named database.

    Checks primary env vars first, then defaults.

    Parameters
    ----------
    db_name :
        Name of the database ('pipeline', etc.)

    Returns
    -------
        Absolute path to the SQLite database file

    Raises
    ------
    ValueError
        If db_name is not recognized

    """
    if db_name not in DEFAULT_DB_PATHS:
        raise ValueError(
            f"Unknown database name: {db_name}. Valid names: {list(DEFAULT_DB_PATHS.keys())}"
        )

    # Fail fast if any legacy env var is set for this DB
    legacy_var = LEGACY_ENV_VARS.get(db_name)
    if legacy_var and os.environ.get(legacy_var):
        raise ValueError(
            f"Legacy environment variable {legacy_var} is no longer supported. "
            "Use PIPELINE_DB to configure the unified database path."
        )

    # Check primary env var
    env_var = DB_ENV_VARS.get(db_name)
    if env_var:
        path = os.environ.get(env_var)
        if path:
            return path

    return DEFAULT_DB_PATHS[db_name]


def get_db_url(db_name: DatabaseName, in_memory: bool = False) -> str:
    """Get SQLAlchemy database URL for a named database.

    Parameters
    ----------
    db_name :
        Name of the database
    in_memory :
        If True, use in-memory SQLite for testing

    Returns
    -------
        SQLAlchemy connection URL

    """
    if in_memory:
        return "sqlite:///:memory:"

    db_path = get_db_path(db_name)
    return f"sqlite:///{db_path}"


def _setup_sqlite_wal_mode(dbapi_connection, connection_record):
    """Set up SQLite WAL mode and other pragmas on connection.

    This is called for every new connection to ensure proper configuration.

    Parameters
    ----------
    dbapi_connection :

    connection_record :


    """
    cursor = dbapi_connection.cursor()

    # Enable WAL mode for concurrent reads/writes
    cursor.execute("PRAGMA journal_mode=WAL")

    # Enable foreign key constraints
    cursor.execute("PRAGMA foreign_keys=ON")

    # Synchronous mode NORMAL is faster while still safe with WAL
    cursor.execute("PRAGMA synchronous=NORMAL")

    # Increase cache size for better performance (64MB)
    cursor.execute("PRAGMA cache_size=-65536")

    # Memory-mapped I/O size (256MB)
    cursor.execute("PRAGMA mmap_size=268435456")

    cursor.close()


def get_engine(
    db_name: DatabaseName,
    in_memory: bool = False,
    echo: bool = False,
) -> Engine:
    """Get or create a SQLAlchemy engine for a database.

    Engines are cached and reused. Each engine is configured with:
    - WAL journal mode for concurrent access
    - 30 second timeout for lock contention
    - Foreign key enforcement
    - Optimized cache and mmap settings

    Note
    ----
    All domain databases (products, cal_registry, hdf5, ingest, data_registry)
    share the same engine since they all point to pipeline.sqlite3.

    Parameters
    ----------
    db_name : DatabaseName
        Name of the database.
    in_memory : bool, optional
        If True, create an in-memory database (for testing). Default is False.
    echo : bool, optional
        If True, log all SQL statements. Default is False.

    Returns
    -------
    Engine
        SQLAlchemy Engine instance.

    Examples
    --------
    >>> engine = get_engine("pipeline")
    >>> Base.metadata.create_all(engine)
    """
    # Use actual file path as cache key to share engines for unified DB
    db_path = get_db_path(db_name) if not in_memory else ":memory:"
    cache_key = f"{db_path}:{'memory' if in_memory else 'file'}"

    with _lock:
        if cache_key in _engines:
            return _engines[cache_key]

        db_url = get_db_url(db_name, in_memory=in_memory)

        # Configure engine
        if in_memory:
            # In-memory databases need special pooling to persist
            engine = create_engine(
                db_url,
                echo=echo,
                poolclass=StaticPool,
                connect_args={
                    "check_same_thread": SQLITE_CHECK_SAME_THREAD,
                },
            )
        else:
            engine = create_engine(
                db_url,
                echo=echo,
                connect_args={
                    "timeout": SQLITE_TIMEOUT,
                    "check_same_thread": SQLITE_CHECK_SAME_THREAD,
                },
                pool_pre_ping=True,  # Check connection health before use
            )

        # Set up WAL mode and other pragmas for new connections
        event.listen(engine, "connect", _setup_sqlite_wal_mode)

        _engines[cache_key] = engine
        logger.debug(f"Created engine for database '{db_name}' at {db_url}")

        return engine


def get_session_factory(
    db_name: DatabaseName,
    in_memory: bool = False,
) -> sessionmaker:
    """Get or create a session factory for a database.

    Session factories are cached and reused.

    Parameters
    ----------
    db_name :
        Name of the database
    in_memory :
        If True, use in-memory database

    Returns
    -------
    try
        # do work
        session.commit()
    finally
        session.close()

    """
    cache_key = f"{db_name}:{'memory' if in_memory else 'file'}"

    with _lock:
        if cache_key in _session_factories:
            return _session_factories[cache_key]

        engine = get_engine(db_name, in_memory=in_memory)
        factory = sessionmaker(
            bind=engine,
            autocommit=False,
            autoflush=True,
            expire_on_commit=True,
        )

        _session_factories[cache_key] = factory
        return factory


def get_scoped_session(
    db_name: DatabaseName,
    in_memory: bool = False,
) -> scoped_session:
    """Get or create a thread-local scoped session for a database.

        Scoped sessions provide thread-safe session management, ideal for
        multi-threaded contexts like the streaming converter.

    Parameters
    ----------
    db_name : DatabaseName
        Name of the database
    in_memory : bool, optional
        If True, use in-memory database, by default False

    Returns
    -------
        Session
        Thread-local scoped session factory

    Examples
    --------
        def worker_thread():
        session = Session()
        try:
        # do work
        session.commit()
        finally:
        Session.remove()  # Clean up thread-local session
    """
    cache_key = f"{db_name}:{'memory' if in_memory else 'file'}"

    with _lock:
        if cache_key in _scoped_sessions:
            return _scoped_sessions[cache_key]

        factory = get_session_factory(db_name, in_memory=in_memory)
        scoped = scoped_session(factory)

        _scoped_sessions[cache_key] = scoped
        return scoped


@contextmanager
def get_session(
    db_name: DatabaseName,
    in_memory: bool = False,
) -> Generator[Session, None, None]:
    """Context manager for safe session handling.

    Automatically commits on success and rolls back on exception.
    Session is closed after the context exits.

    Parameters
    ----------
    db_name :
        Name of the database
    in_memory :
        If True, use in-memory database
    """
    factory = get_session_factory(db_name, in_memory=in_memory)
    session = factory()

    try:
        yield session
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_readonly_session(
    db_name: DatabaseName,
    in_memory: bool = False,
) -> Generator[Session, None, None]:
    """Context manager for read-only session handling.

    Does not commit - use for queries only.

    Parameters
    ----------
    db_name :
        Name of the database
    in_memory :
        If True, use in-memory database
    """
    factory = get_session_factory(db_name, in_memory=in_memory)
    session = factory()

    try:
        yield session
    finally:
        session.close()


# =============================================================================
# Database Initialization
# =============================================================================


def init_database(
    db_name: DatabaseName,
    in_memory: bool = False,
) -> None:
    """Initialize database tables if they don't exist.

    Creates all tables defined in the appropriate Base for the database.
    Safe to call multiple times - existing tables are not modified.

    Parameters
    ----------
    db_name : DatabaseName
        Name of the database to initialize.
    in_memory : bool, optional
        If True, use in-memory database. Default is False.

    Examples
    --------
    >>> init_database("pipeline")  # Creates all tables in pipeline.sqlite3
    """
    from .models import (
        CalRegistryBase,
        DataRegistryBase,
        HDF5Base,
        IngestBase,
        ProductsBase,
    )

    engine = get_engine(db_name, in_memory=in_memory)

    bases = [ProductsBase, CalRegistryBase, HDF5Base, IngestBase, DataRegistryBase]
    if db_name == "pipeline":
        for base in bases:
            base.metadata.create_all(engine)
        logger.info("Initialized database: pipeline")
        return
    logger.warning(f"No model base defined for database: {db_name}")


def reset_engines() -> None:
    """Reset all cached engines and session factories.

    Useful for testing or when database files are replaced.

    """
    global _engines, _session_factories, _scoped_sessions

    with _lock:
        # Dispose all engines
        for engine in _engines.values():
            engine.dispose()

        # Remove scoped sessions
        for scoped in _scoped_sessions.values():
            scoped.remove()

        _engines.clear()
        _session_factories.clear()
        _scoped_sessions.clear()

    logger.debug("Reset all database engines and session factories")


# =============================================================================
# Compatibility layer for gradual migration
# =============================================================================


def get_raw_connection(db_name: DatabaseName) -> Connection:
    """Get a raw SQLAlchemy connection for legacy code migration.

        This provides a connection that can be used with raw SQL while
        still benefiting from proper connection management.

    Parameters
    ----------
    db_name : DatabaseName
        Name of the database.

    Returns
    -------
        Connection
        SQLAlchemy Connection object.

    Examples
    --------
        # For gradual migration of raw SQL code
        conn = get_raw_connection("pipeline")
        result = conn.execute(text("SELECT * FROM images LIMIT 10"))
        rows = result.fetchall()
        conn.close()
    """
    engine = get_engine(db_name)
    return engine.connect()
