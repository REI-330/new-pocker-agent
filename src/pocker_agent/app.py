"""v0.4 application surface: capabilities, reference games, playable sessions.

This app talks only to ``core``. The legacy ``api.py`` (v0.2 family engines) is
kept only as a test oracle during the strangler migration and is not served by
this application.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import SessionStore, capability_matrix, coverage_report, list_reference_games
from .storage import data_path


class SessionInput(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)
    seed: int | None = None


class ActionInput(BaseModel):
    revision: int = Field(ge=0)
    card_index: int = Field(default=0, ge=0)
    expression: str = Field(default="", max_length=256)
    declared_suit: str = Field(default="", max_length=16)


def create_app(path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Pocker Agent", version="0.4.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173",
                       "http://127.0.0.1:5174", "http://127.0.0.1:4173"],
        allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    store = SessionStore(path or data_path())
    app.state.session_store = store

    assets = Path(__file__).parent / "assets"
    app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse(status_code=422, content={"detail": "; ".join(
            f"{'.'.join(map(str, issue['loc']))}: {issue['msg']}" for issue in error.errors())})

    @app.exception_handler(ValueError)
    async def bad_value(request, error):
        message = str(error)
        status = 409 if message.startswith("stale_revision") else 422
        return JSONResponse(status_code=status, content={"detail": message})

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse(status_code=404, content={"detail": "牌局不存在，请重新开始"})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.4.0"}

    @app.get("/api/capabilities")
    def capabilities():
        return {"matrix": capability_matrix(), "coverage": coverage_report()}

    @app.get("/api/games")
    def games():
        return {"games": list_reference_games(), "coverage": coverage_report()}

    @app.post("/api/sessions")
    def create_session(payload: SessionInput):
        return store.snapshot(store.create(payload.game_id, payload.seed))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        return store.snapshot(store.get(session_id))

    @app.post("/api/sessions/{session_id}/actions/{action}")
    def act(session_id: str, action: str, payload: ActionInput):
        return store.act(session_id, action, payload.revision,
                         card_index=payload.card_index, expression=payload.expression,
                         declared_suit=payload.declared_suit)

    # Single-port demo: serve the built frontend when it exists.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
