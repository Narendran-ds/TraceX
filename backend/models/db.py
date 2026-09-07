"""SQLite access layer.

Thin deliberately: the schema is fixed (master report §11) and an ORM would add
friction rather than safety. Connections are per-call so the FastAPI threadpool
and the CLI scripts can share the same file without cross-thread issues.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from backend.config import DB_PATH, SCHEMA_PATH


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Transactional connection. Commits on success, rolls back on error."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """Create the schema if it does not exist. Idempotent."""
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        conn.executescript(ddl)
        conn.commit()
    finally:
        conn.close()


def reset_db(db_path: Optional[Path] = None) -> None:
    """Drop all rows and recreate. Used by seeding and tests."""
    path = Path(db_path) if db_path else DB_PATH
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
    init_db(path)


# --- row helpers -----------------------------------------------------------

def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def loads(value: Optional[str], default: Any) -> Any:
    """Parse a JSON column, tolerating NULL and malformed values."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def record_audit(
    conn: sqlite3.Connection,
    case_id: str,
    action: str,
    actor: str,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (id, case_id, action, detail, actor, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), case_id, action, dumps(detail or {}), actor, utc_now_iso()),
    )
