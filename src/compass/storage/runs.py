"""Onboarding-run history and cost aggregation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class RunsRepo:
    def __init__(self, db: Any) -> None:
        self._col = db["runs"]
        self._col.create_index("username")
        self._col.create_index("started_at")

    def record(
        self,
        *,
        username: str,
        repo: str,
        role: str | None,
        difficulty: str,
        pack_dir: str,
        duration_seconds: float,
        total_bobcoins: float,
        subagent_bobcoins: dict[str, float],
        total_tokens: int,
        content_kind: str,
        status: str,
        error: str | None = None,
    ) -> str:
        doc = {
            "username": username,
            "repo": repo,
            "role": role,
            "difficulty": difficulty,
            "pack_dir": pack_dir,
            "started_at": datetime.now(timezone.utc),
            "duration_seconds": float(duration_seconds),
            "total_bobcoins": float(total_bobcoins),
            "subagent_bobcoins": {k: float(v) for k, v in subagent_bobcoins.items()},
            "total_tokens": int(total_tokens),
            "content_kind": content_kind,
            "status": status,
            "error": error,
        }
        res = self._col.insert_one(doc)
        return str(res.inserted_id)

    def list_for(self, username: str, *, limit: int = 25) -> list[dict[str, Any]]:
        cur = self._col.find({"username": username}).sort("started_at", -1).limit(limit)
        return list(cur)

    def totals_for(self, username: str) -> dict[str, float | int]:
        """Aggregate the user's lifetime spend and run count."""
        docs = list(self._col.find({"username": username}))
        total_bob = sum(float(d.get("total_bobcoins", 0.0)) for d in docs)
        total_toks = sum(int(d.get("total_tokens", 0)) for d in docs)
        return {
            "runs": len(docs),
            "successful_runs": sum(1 for d in docs if d.get("status") == "success"),
            "total_bobcoins": total_bob,
            "total_tokens": total_toks,
        }
