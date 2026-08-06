"""Pytest fixtures: an isolated temp SQLite DB pointed at by the app settings."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# scripts/ is not a package (its modules are run directly), so import them the same way
# sync.py does -- as top-level modules. Importing "scripts.freeport" instead would make
# mypy see the same file under two module names.
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from config import settings
    from degiro_explorer import store

    db = tmp_path / "test.db"
    monkeypatch.setattr(settings, "db_path", str(db))
    store.init_db()
    return db
