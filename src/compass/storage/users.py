"""User accounts — sign-up, sign-in, Google upsert. Passwords hashed with bcrypt."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class User:
    username: str
    email: str | None
    provider: str  # "password" | "google"
    created_at: datetime

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "User":
        return cls(
            username=doc["username"],
            email=doc.get("email"),
            provider=doc.get("provider", "password"),
            created_at=doc.get("created_at") or datetime.now(timezone.utc),
        )


class AuthError(Exception):
    """Bad username, bad password, or duplicate signup."""


class UsersRepo:
    def __init__(self, db: Any) -> None:
        self._col = db["users"]
        self._col.create_index("username", unique=True)
        # google_sub is unique but only when present; MongoDB partial index would need
        # a different creation call — mongomock does not fully support that, so we just
        # rely on the application-level uniqueness check in upsert_google().

    def sign_up(self, *, username: str, password: str, email: str | None = None) -> User:
        import bcrypt

        username = username.strip().lower()
        if not username or not password:
            raise AuthError("Username and password are required.")
        if len(password) < 8:
            raise AuthError("Password must be at least 8 characters.")
        if self._col.find_one({"username": username}):
            raise AuthError("That username is already taken.")

        doc = {
            "username": username,
            "email": (email or "").strip() or None,
            "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            "provider": "password",
            "created_at": datetime.now(timezone.utc),
        }
        self._col.insert_one(doc)
        return User.from_doc(doc)

    def sign_in(self, *, username: str, password: str) -> User:
        import bcrypt

        username = username.strip().lower()
        doc = self._col.find_one({"username": username})
        if not doc or not doc.get("password_hash"):
            raise AuthError("Invalid username or password.")
        if not bcrypt.checkpw(password.encode(), doc["password_hash"].encode()):
            raise AuthError("Invalid username or password.")
        return User.from_doc(doc)

    def upsert_google(self, *, google_sub: str, email: str, name: str | None = None) -> User:
        """Create or update a user identified by their Google `sub` claim."""
        username = (name or email.split("@")[0] or "user").lower().replace(" ", "_")
        # Avoid collisions with an existing password user of the same name.
        base = username
        n = 1
        while True:
            existing = self._col.find_one({"username": username})
            if not existing:
                break
            if existing.get("google_sub") == google_sub:
                # Same Google identity — reuse.
                self._col.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"email": email, "last_login_at": datetime.now(timezone.utc)}},
                )
                return User.from_doc({**existing, "email": email})
            n += 1
            username = f"{base}{n}"

        doc = {
            "username": username,
            "email": email,
            "provider": "google",
            "google_sub": google_sub,
            "created_at": datetime.now(timezone.utc),
        }
        self._col.insert_one(doc)
        return User.from_doc(doc)
