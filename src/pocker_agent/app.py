"""v0.4 application surface: capabilities, games, sessions, and the agent loop.

This app talks only to ``core`` and ``agent``. Every playable game passes the
playtest gate: reference games at build time, agent-composed games at
``finalize`` time (enforced again by ``SessionStore.register_plan``).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import TOOL_SCHEMAS, run_loop
from .configuration import ConfigInput, ConfigStore
from .core import SessionStore, capability_matrix, core_registry, coverage_report
from .llm import OpenAICompatibleClient
from .storage import data_path


class SessionInput(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)
    seed: int | None = None


class ActionInput(BaseModel):
    revision: int = Field(ge=0)
    card_index: int = Field(default=0, ge=0)
    expression: str = Field(default="", max_length=256)
    declared_suit: str = Field(default="", max_length=16)


class LoopInput(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    max_steps: int = Field(default=12, ge=1, le=24)


def create_app(path: Path | None = None, vault=None, model_factory=None) -> FastAPI:
    app = FastAPI(title="Pocker Agent", version="0.4.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173",
                       "http://127.0.0.1:5174", "http://127.0.0.1:4173"],
        allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    database = path or data_path()
    config = ConfigStore(database, vault)
    store = SessionStore(database)
    app.state.session_store = store
    app.state.config_store = config

    def default_model():
        saved = config.read()
        if not saved.public()["configured"]:
            raise ValueError("请先保存模型配置")
        return OpenAICompatibleClient.from_config(saved)

    make_model = model_factory or default_model

    app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")

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
        return {"games": store.list_games(), "coverage": coverage_report()}

    @app.get("/api/agent/tools")
    def agent_tools():
        return {"meta_tools": TOOL_SCHEMAS, "game_tools": core_registry().export()}

    @app.get("/api/agent/config")
    def get_config():
        return config.read().public()

    @app.post("/api/agent/config")
    def save_config(payload: ConfigInput):
        return config.save(payload)

    @app.post("/api/agent/models")
    def discover(payload: ConfigInput):
        draft = config.draft(payload, require_model=False)
        return {"models": OpenAICompatibleClient.from_config(draft).list_models(),
                "base_url": draft.base_url}

    @app.post("/api/agent/test-connection")
    def test_connection(payload: ConfigInput):
        draft = config.draft(payload)
        result = OpenAICompatibleClient.from_config(draft).complete(
            [{"role": "user", "content": "Reply with OK."}])
        return {"ok": bool(result), "model": draft.model, "base_url": draft.base_url}

    @app.post("/api/agent/loop")
    def agent_loop(payload: LoopInput):
        result = run_loop(payload.goal, make_model(), max_steps=payload.max_steps)
        if result.finalized and result.plan and result.ir:
            store.register_plan(result.ir["game_id"], result.plan, result.playtest or {},
                                result.ir.get("title", ""))
        return result.as_dict()

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

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
