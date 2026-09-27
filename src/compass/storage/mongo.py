"""MongoDB client factory with an in-memory `mongomock` fallback.

Callers do not need to know which backend is in use — both expose the same
pymongo API on top of the returned `Database` object.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def get_db() -> Any:
    """Return a `Database` object. Real MongoDB if `MONGO_URI` is set and reachable, else mongomock."""
    uri = os.getenv("MONGO_URI", "").strip()
    db_name = os.getenv("MONGO_DB", "compass")

    if uri:
        try:
            from pymongo import MongoClient  # type: ignore
            from pymongo.errors import PyMongoError  # type: ignore
            client = MongoClient(uri, serverSelectionTimeoutMS=3000)
            # Force an early connection so we can fall back cleanly.
            client.admin.command("ping")
            return client[db_name]
        except Exception:
            # Any failure — bad URI, no network, auth — falls back to mongomock.
            pass

    import mongomock
    return mongomock.MongoClient()[db_name]


def is_real_mongo() -> bool:
    """Return True if the current db is a real MongoDB connection (not mongomock)."""
    db = get_db()
    # Real pymongo Database has a `.client` attribute whose class name is `MongoClient`;
    # mongomock also has one but from the `mongomock.mongo_client` module.
    return db.client.__class__.__module__.startswith("pymongo")
