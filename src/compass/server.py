"""FastAPI app exposing /onboard, /ask, /healthz. Bearer-token auth."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from compass.application.ask import answer_question
from compass.config import ServerConfig
from compass.domain.models import AskAnswer, AskRequest


class OnboardRequest(BaseModel):
    repo: str
    role: str | None = None
    difficulty: str = "easy"


def _auth(cfg: ServerConfig) -> Callable[[str], None]:
    async def check(authorization: str = Header(default="")) -> None:
        expected = f"Bearer {cfg.api_token}"
        if authorization != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token")
    return check


def build_app(*, pack_dir: Path) -> FastAPI:
    cfg = ServerConfig.from_env()
    app = FastAPI(title="Compass", version="0.1.0")
    auth = Depends(_auth(cfg))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/ask", response_model=AskAnswer, dependencies=[auth])
    def ask(req: AskRequest) -> AskAnswer:
        return answer_question(pack_dir=pack_dir, question=req.question, top_k=req.top_k)

    @app.post("/onboard", dependencies=[auth])
    def onboard(_req: OnboardRequest) -> dict[str, str]:
        # TODO(compass): enqueue a run and return a run_id; do not block the request thread.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "onboard queue not wired yet")

    return app
