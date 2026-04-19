"""Per-user DB isolation tests.

Confirms that ``db_engines.user_engine`` gives two users separate SQLite files
and that writes to one do not leak into the other.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

import app.db_engines as db_engines
from app.models import Food


@pytest.fixture
def tmp_data_root(monkeypatch, tmp_path):
    """Point db_engines at an empty tmp dir and clear engine caches."""

    monkeypatch.setattr(db_engines, "_data_root", lambda: tmp_path.resolve())
    # Reset module-level caches so the fixture is hermetic.
    monkeypatch.setattr(db_engines, "_auth_engine", None, raising=False)
    db_engines._user_engines.clear()
    db_engines._user_migrate_locks.clear()
    yield tmp_path
    # Dispose of any engines opened during the test so Windows releases the
    # SQLite file handles before tmp_path is cleaned up.
    for user_id in list(db_engines._user_engines.keys()):
        db_engines.dispose_user_engine(user_id)
    if db_engines._auth_engine is not None:
        db_engines._auth_engine.dispose()
        db_engines._auth_engine = None


def _make_food(name: str, user_id: str) -> None:
    engine = db_engines.user_engine(user_id)
    with Session(engine) as sess:
        sess.add(
            Food(
                name=name,
                calories_per_serving=10,
                fat_per_serving=1,
                carbs_per_serving=1,
                protein_per_serving=1,
                serving_size_grams=100,
            )
        )
        sess.commit()


def _list_food_names(user_id: str) -> list[str]:
    engine = db_engines.user_engine(user_id)
    with Session(engine) as sess:
        return [f.name for f in sess.exec(select(Food))]


def test_per_user_files_are_distinct(tmp_data_root):
    alice = "alice-id"
    bob = "bob-id"
    _ = db_engines.user_engine(alice)
    _ = db_engines.user_engine(bob)

    alice_db = db_engines.user_db_path(alice)
    bob_db = db_engines.user_db_path(bob)
    assert alice_db.exists()
    assert bob_db.exists()
    assert alice_db != bob_db
    # They must live under data/users/<id>/diet_tracker.db.
    assert alice_db.parent.name == alice
    assert bob_db.parent.name == bob


def test_no_data_leakage_between_users(tmp_data_root):
    alice = "alice-id"
    bob = "bob-id"

    _make_food("Alice's Apple", alice)
    _make_food("Bob's Banana", bob)

    assert _list_food_names(alice) == ["Alice's Apple"]
    assert _list_food_names(bob) == ["Bob's Banana"]


def test_user_engine_is_cached_per_user(tmp_data_root):
    alice = "alice-id"
    e1 = db_engines.user_engine(alice)
    e2 = db_engines.user_engine(alice)
    assert e1 is e2


def test_dispose_user_engine_evicts_cache(tmp_data_root):
    alice = "alice-id"
    e1 = db_engines.user_engine(alice)
    db_engines.dispose_user_engine(alice)
    assert alice not in db_engines._user_engines
    # Getting it again produces a fresh engine.
    e2 = db_engines.user_engine(alice)
    assert e1 is not e2
