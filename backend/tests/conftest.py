"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """A throwaway SQLite database, isolated per test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUNDTRAIL_DB", str(db_path))

    from backend import config
    from backend.models import db as db_module

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    db_module.init_db(db_path)
    return db_path
