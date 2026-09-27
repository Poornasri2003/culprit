"""Persistence layer for Compass — users and run history.

Uses MongoDB when `MONGO_URI` is set. Falls back to an in-memory `mongomock`
database otherwise, so the app runs end-to-end without any provisioning
during development.
"""
from compass.storage.mongo import get_db
from compass.storage.runs import RunsRepo
from compass.storage.users import UsersRepo

__all__ = ["get_db", "UsersRepo", "RunsRepo"]
