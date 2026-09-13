"""v0.4 application surface: capabilities, games, sessions, and the agent loop.

This app talks only to ``core`` and ``agent``. Every playable game passes the
playtest gate: reference games at build time, agent-composed games at
``finalize`` time (enforced again by ``SessionStore.register_plan``).
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import DESIGN_TOOL_SCHEMAS, TOOL_SCHEMAS, DesignService, DesignStore, run_design_loop, run_loop
from .configuration import ConfigInput, ConfigStore
from .core import (
    PLAN_MACROS,
    SessionStore,
    capability_matrix,
    core_registry,
    coverage_report,
    default_macros,
    promotion_report,
)
from .llm import OpenAICompatibleClient
from .storage import data_path

VERSION = "0.4.0"
PACKAGE_ROOT = Path(__file__).resolve().parent
DIST = PACKAGE_ROOT.parents[1] / "frontend" / "dist"


def code_fingerprint() -> str:
    """Hash of the package sources *as imported*.

    It is computed once at import time on purpose: recomputing it on every
    request would always match the working tree, which is exactly the signal we
    want to keep. A long-running process keeps the fingerprint of the code it
    actually loaded, so a mismatch against the working tree means "restart me".
    """
    digest = hashlib.sha256()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        digest.update(path.relative_to(PACKAGE_ROOT).as_posix().encode())
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:                                     # pragma: no cover
            continue
        digest.update(b"\0")
    return digest.hexdigest()[:12]


CODE_FINGERPRINT = code_fingerprint()


def dist_assets() -> list[str]:
    """The bundles the served page asks for, read from disk on each call."""
    index = DIST / "index.html"
    if not index.is_file():
        return []
    try:
        html = index.read_text(encoding="utf-8", errors="replace")
    except OSError:                                         # pragma: no cover
        return []
    return sorted(set(re.findall(r'(?:src|href)="(/(?:static|assets)/[^"]+)"', html)))


class SessionInput(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)
    seed: int | None = None


class DesignCreateInput(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=8000)


class DesignUpdateInput(BaseModel):
    """An optimistic-locked write to a design session (M4-1).

    ``expected_revision`` is mandatory: a write without it cannot be checked
    against the stored revision, so the schema refuses it rather than guessing.
    Only the fields present are changed; ``request_id`` makes a retry idempotent.
    """

    expected_revision: int = Field(ge=0)
    request_id: str | None = Field(default=None, max_length=64)
    event: str = Field(default="updated", max_length=64)
    description: str | None = Field(default=None, max_length=8000)
    ir: dict | None = None
    diagnosis: dict | None = None
    status: str | None = Field(default=None, max_length=16)
    context: dict | None = None


class ChatTurn(BaseModel):
    """One prior chat turn, so a design conversation can span requests."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class DesignMessageInput(BaseModel):
    """A natural-language design turn against one durable design session."""

    message: str = Field(max_length=4000)
    expected_revision: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=1, le=24)


class ActionInput(BaseModel):
    revision: int = Field(ge=0)
    card_index: int = Field(default=0, ge=0)
    expression: str = Field(default="", max_length=256)
    declared_suit: str = Field(default="", max_length=16)
    amount: int = Field(default=0, ge=0)
    request_id: str | None = Field(default=None, max_length=64)


class LoopInput(BaseModel):
    message: str = Field(default="", max_length=4000)
    goal: str = Field(default="", max_length=4000)          # kept for older clients
    messages: list[ChatTurn] = Field(default_factory=list, max_length=60)
    max_steps: int = Field(default=12, ge=1, le=24)
    design_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)  # legacy alias
    expected_revision: int | None = Field(default=None, ge=0)


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
    designs = DesignStore(database)
    app.state.session_store = store
    app.state.design_store = designs
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
        conflict = ("stale_revision", "plan_changed", "plan_already_registered",
                    "game_id_reserved", "request_id_conflict")
        status = 409 if message.startswith(conflict) else 422
        return JSONResponse(status_code=status, content={"detail": message})

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        key = error.args[0] if error.args else ""
        message = "设计会话不存在" if str(key).startswith("design_") else "牌局不存在，请重新开始"
        return JSONResponse(status_code=404, content={"detail": message})

    @app.get("/health")
    def health():
        assets = dist_assets()
        return {"status": "ok", "version": VERSION, "pid": os.getpid(),
                "code": CODE_FINGERPRINT, "assets": assets,
                "assets_present": all((DIST / url.lstrip("/")).is_file() for url in assets)}

    @app.get("/api/capabilities")
    def capabilities():
        return {"matrix": capability_matrix(), "coverage": coverage_report(),
                "macro_promotion": promotion_report(PLAN_MACROS, default_macros())}

    @app.get("/api/games")
    def games():
        return {"games": store.list_games(), "coverage": coverage_report()}

    @app.post("/api/designs")
    def create_design(payload: DesignCreateInput):
        return designs.create(payload.game_id, payload.description).as_dict()

    @app.get("/api/designs")
    def list_designs():
        return {"designs": designs.list_sessions()}

    @app.get("/api/designs/{session_id}")
    def get_design(session_id: str):
        return designs.get(session_id).as_dict()

    @app.post("/api/designs/{session_id}/update")
    def update_design(session_id: str, payload: DesignUpdateInput):
        changes: dict[str, object] = {}
        if payload.description is not None:
            changes["description"] = payload.description
        if payload.ir is not None:
            changes["ir"] = payload.ir
        if payload.diagnosis is not None:
            changes["diagnosis"] = payload.diagnosis
        if payload.status is not None:
            changes["status"] = payload.status
        if payload.context is not None:
            changes["context"] = payload.context
        return designs.commit(session_id, payload.expected_revision,
                              request_id=payload.request_id, event=payload.event,
                              **changes).as_dict()

    @app.post("/api/designs/{session_id}/messages")
    def design_messages(session_id: str, payload: DesignMessageInput):
        """One design turn against an existing session (M4-7)."""
        message = payload.message.strip()
        if not message:
            raise ValueError("请输入玩法描述")
        current = designs.get(session_id)                    # KeyError -> 404
        if (payload.expected_revision is not None
                and payload.expected_revision != current.revision):
            raise ValueError(
                f"stale_revision: 设计会话已更新（当前 {current.revision}，"
                f"期望 {payload.expected_revision}）")
        service = DesignService(designs, session_id)
        budget = {"max_decisions": payload.max_steps} if payload.max_steps else None
        result = run_design_loop(service, session_id, message, make_model(), budget)
        body = result.as_dict()
        body["registered"] = False
        body["session"] = designs.get(session_id).as_dict()
        return body

    @app.get("/api/agent/tools")
    def agent_tools():
        return {"meta_tools": TOOL_SCHEMAS, "game_tools": core_registry().export()}

    @app.get("/api/agent/design-tools")
    def agent_design_tools():
        """The design-session tool table (separate from the legacy loop's)."""
        return {"design_tools": DESIGN_TOOL_SCHEMAS}

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
        goal = (payload.message or payload.goal).strip()
        if not goal:
            raise ValueError("请输入玩法描述")
        design_id = payload.design_id or payload.session_id
        if design_id:
            # M4-7: route to the design-session loop. The legacy known-family
            # loop below is unchanged when neither id is given.
            current = designs.get(design_id)                 # KeyError -> 404
            if (payload.expected_revision is not None
                    and payload.expected_revision != current.revision):
                raise ValueError(
                    f"stale_revision: 设计会话已更新（当前 {current.revision}，"
                    f"期望 {payload.expected_revision}）")
            service = DesignService(designs, design_id)
            result = run_design_loop(service, design_id, goal, make_model(),
                                     {"max_decisions": payload.max_steps})
            body = result.as_dict()
            body["registered"] = False
            return body
        result = run_loop(goal, make_model(),
                          history=[message.model_dump() for message in payload.messages],
                          max_steps=payload.max_steps)
        registered = False
        if result.finalized and result.plan and result.ir:
            try:
                # Host gate: re-verify with a host-chosen policy, then register an
                # immutable artifact. The loop's own playtest report is not used
                # as evidence (ADR-0008).
                store.verify_and_register_plan(result.ir["game_id"], result.plan,
                                               result.ir.get("title", ""))
                registered = True
            except ValueError as error:
                # The design itself is still valid and worth showing; only the
                # registration was refused (a reserved or already-used id).
                body = result.as_dict()
                body["registered"] = False
                body["registration_error"] = str(error)
                return body
        body = result.as_dict()
        body["registered"] = registered
        return body

    @app.post("/api/sessions")
    def create_session(payload: SessionInput):
        return store.snapshot(store.create(payload.game_id, payload.seed))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        return store.snapshot(store.get(session_id))

    @app.post("/api/sessions/{session_id}/actions/{action}")
    def act(session_id: str, action: str, payload: ActionInput):
        return store.act(session_id, action, payload.revision,
                         request_id=payload.request_id,
                         card_index=payload.card_index, expression=payload.expression,
                         declared_suit=payload.declared_suit, amount=payload.amount)

    if DIST.is_dir():
        app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
    return app


app = create_app()
