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
from pydantic import BaseModel, ConfigDict, Field

from .agent import (
    DESIGN_TOOL_SCHEMAS,
    TOOL_SCHEMAS,
    DesignRunStore,
    DesignService,
    DesignStore,
    normalize_budget,
    run_design_loop,
    run_loop,
    run_status_for,
)
from .configuration import ConfigInput, ConfigStore
from .core import (
    PLAN_MACROS,
    ComposedRulesIR,
    SessionStore,
    capability_matrix,
    core_registry,
    coverage_report,
    default_macros,
    promotion_report,
)
from .core.ir import parse_design_ir
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
    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(min_length=1, max_length=64)
    seed: int | None = None
    version: int | None = Field(default=None, ge=1)


class DesignCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=8000)


class DesignUpdateInput(BaseModel):
    """An optimistic-locked write to a design session (M4-1).

    ``expected_revision`` is mandatory: a write without it cannot be checked
    against the stored revision, so the schema refuses it rather than guessing.
    Only the fields present are changed; ``request_id`` makes a retry idempotent.
    """

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class DesignMessageInput(BaseModel):
    """A natural-language design turn against one durable design session."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(max_length=4000)
    expected_revision: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=1, le=24)
    request_id: str | None = Field(default=None, max_length=64)


class DesignVerifyInput(BaseModel):
    """Host-run formal verification of the session's current rules (M5-1)."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, max_length=64)


class DesignConfirmInput(BaseModel):
    """The user's confirmation of one exact ``ir_hash`` (M5-1).

    This is deliberately a separate HTTP action, never a design tool: the model
    can propose rules but only a user can confirm them (ADR-0008/0015).
    """

    model_config = ConfigDict(extra="forbid")

    ir_hash: str = Field(min_length=1, max_length=64)
    expected_revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, max_length=64)


class DesignPublishInput(BaseModel):
    """Register an immutable version once confirmation and evidence bind."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=128)
    version: int | None = Field(default=None, ge=1)


class ActionInput(BaseModel):
    """The legacy per-action payload (kept as a thin adapter, ADR-0007)."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    card_index: int = Field(default=0, ge=0)
    expression: str = Field(default="", max_length=256)
    declared_suit: str = Field(default="", max_length=16)
    amount: int = Field(default=0, ge=0)
    request_id: str | None = Field(default=None, max_length=64)


class ActionRequest(BaseModel):
    """A generic, descriptor-driven action (M5-2).

    ``input_values`` is keyed by the input ids the plan declares in ``view()``;
    the store validates them and the interpreter remains the final authority.
    """

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=64)
    input_values: dict = Field(default_factory=dict)
    revision: int = Field(ge=0)
    request_id: str | None = Field(default=None, max_length=64)


class LoopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    runs = DesignRunStore(database)
    app.state.session_store = store
    app.state.design_store = designs
    app.state.design_run_store = runs
    app.state.config_store = config
    # A crash can leave a run marked ``running``; reap it now that we are back.
    runs.recover_stale()

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
        if message.startswith("version_not_found"):
            return JSONResponse(status_code=404, content={"detail": "游戏版本不存在"})
        conflict = ("stale_revision", "plan_changed", "plan_already_registered",
                    "game_id_reserved", "request_id_conflict",
                    "artifact_version_conflict")
        status = 409 if message.startswith(conflict) else 422
        return JSONResponse(status_code=status, content={"detail": message})

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        key = str(error.args[0]) if error.args else ""
        if key.startswith("design_run"):
            message = "设计运行不存在"
        elif key.startswith("design_"):
            message = "设计会话不存在"
        elif key.startswith("game_version"):
            message = "游戏版本不存在"
        else:
            message = "牌局不存在，请重新开始"
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

    @app.get("/api/games/{game_id}/versions")
    def list_game_versions(game_id: str):
        """Every immutable version registered for a game id (M5-1)."""
        return {"game_id": game_id,
                "versions": [_artifact_summary(item)
                             for item in store.list_versions(game_id)]}

    @app.get("/api/games/{game_id}/versions/{version}")
    def get_game_version(game_id: str, version: int):
        """One version's rules, artifact summary, evidence and source map (M5-1)."""
        artifact = store.get_artifact(game_id, version)
        if artifact is None:
            raise KeyError("game_version_not_found")
        return {"game_id": game_id, "version": version,
                "artifact": _artifact_summary(artifact),
                "rules": artifact.get("ir"),
                "source_map": artifact.get("source_map") or {},
                "verification": store.get_verification(artifact["verification_id"]),
                "plan": artifact.get("plan")}

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
        """One design turn against an existing session (M4-7), recorded as a run."""
        message = payload.message.strip()
        if not message:
            raise ValueError("请输入玩法描述")
        current = designs.get(session_id)                    # KeyError -> 404
        if payload.request_id:
            prior = runs.find_by_request(session_id, payload.request_id)
            if prior is not None:
                if prior.status == "running":
                    raise ValueError("request_id_conflict: 相同 request_id 的请求正在处理中")
                if prior.response is not None:
                    return prior.response            # a retried turn replays verbatim
        if (payload.expected_revision is not None
                and payload.expected_revision != current.revision):
            raise ValueError(
                f"stale_revision: 设计会话已更新（当前 {current.revision}，"
                f"期望 {payload.expected_revision}）")
        budget = {"max_decisions": payload.max_steps} if payload.max_steps else None
        run = runs.start(session_id, message, revision=current.revision,
                         budget=normalize_budget(budget), request_id=payload.request_id)
        service = DesignService(designs, session_id)
        try:
            result = run_design_loop(service, session_id, message, make_model(), budget,
                                     expected_revision=payload.expected_revision)
        except ValueError as error:
            # A concurrent writer (or a conflicting retry) owns the session; the
            # run is closed as failed and the conflict propagates as HTTP 409.
            runs.finish(run.run_id, status="failed", kind="error",
                        revision=designs.get(session_id).revision, error=str(error))
            raise
        except Exception as error:                # never leave a run ``running``
            runs.finish(run.run_id, status="failed", kind="error",
                        revision=designs.get(session_id).revision,
                        error=f"turn_failed:{type(error).__name__}")
            raise
        body = result.as_dict()
        body["registered"] = False
        body["run_id"] = run.run_id
        body["session"] = designs.get(session_id).as_dict()
        runs.finish(run.run_id, status=run_status_for(result.kind), kind=result.kind,
                    revision=result.revision, attempts=result.attempts, used=result.used,
                    observations=result.observations, artifact=result.artifact,
                    verification=result.verification, response=body)
        return body

    @app.get("/api/designs/{session_id}/runs")
    def list_design_runs(session_id: str, limit: int = 20):
        designs.get(session_id)                              # KeyError -> 404
        return {"session_id": session_id,
                "runs": runs.list_runs(session_id, limit=max(1, min(limit, 100)))}

    @app.get("/api/designs/{session_id}/runs/{run_id}")
    def get_design_run(session_id: str, run_id: str):
        designs.get(session_id)                              # KeyError -> 404
        run = runs.get(run_id)                               # KeyError -> 404
        if run.session_id != session_id:
            raise KeyError("design_run_not_found")
        return run.as_dict()

    @app.post("/api/designs/{session_id}/verify")
    def verify_design(session_id: str, payload: DesignVerifyInput):
        """Run the host formal gate for the current rules (M5-1).

        This is the same ``verify_game`` tool the model may call, invoked directly
        by the host: the caller supplies no seeds, strategies or plan.
        """
        current = designs.get(session_id)                    # KeyError -> 404
        if (payload.expected_revision is not None
                and payload.expected_revision != current.revision):
            raise ValueError(
                f"stale_revision: 设计会话已更新（当前 {current.revision}，"
                f"期望 {payload.expected_revision}）")
        service = DesignService(designs, session_id)
        observation = service.dispatch("verify_game", {}, request_id=payload.request_id)
        session = designs.get(session_id)
        return {"ok": bool(observation.get("ok")), "observation": observation,
                "revision": session.revision, "session": session.as_dict()}

    @app.post("/api/designs/{session_id}/confirm")
    def confirm_design(session_id: str, payload: DesignConfirmInput):
        """Record the user's confirmation of one exact rule version (M5-1)."""
        current = designs.get(session_id)                    # KeyError -> 404
        if not current.ir or not current.ir_hash:
            raise ValueError("propose_ir_first")
        if payload.ir_hash != current.ir_hash:
            raise ValueError("approval_mismatch: 确认的规则版本与当前草案不一致")
        verification = current.context.get("verification") or {}
        confirmation = {
            "ir_hash": current.ir_hash, "revision": current.revision,
            "verified": bool(verification.get("ok"))
            and verification.get("ir_hash") == current.ir_hash,
            "verification_id": verification.get("verification_id")}
        updated = designs.commit(session_id, payload.expected_revision
                                 if payload.expected_revision is not None
                                 else current.revision,
                                 request_id=payload.request_id, event="confirmed",
                                 context={"confirmation": confirmation})
        return {"confirmed": True, "confirmation": confirmation,
                "session": updated.as_dict()}

    @app.post("/api/designs/{session_id}/publish")
    def publish_design(session_id: str, payload: DesignPublishInput):
        """Register an immutable version once confirmation and evidence bind (M5-1).

        Order is fixed: the user confirmation must name the *current* ``ir_hash``,
        the stored host credential must bind the same rules, and registration then
        re-runs ``publish_composed`` with ``approval_ir_hash`` so a model can never
        register or self-confirm (ADR-0008).
        """
        current = designs.get(session_id)                    # KeyError -> 404
        if not current.ir or not current.ir_hash:
            raise ValueError("propose_ir_first")
        published = current.context.get("published")
        if isinstance(published, dict) and published.get("ir_hash") == current.ir_hash:
            return {"published": True, "idempotent": True, "artifact": published,
                    "session": current.as_dict()}
        confirmation = current.context.get("confirmation") or {}
        if confirmation.get("ir_hash") != current.ir_hash:
            raise ValueError("confirmation_required: 请先核对并确认当前规则版本")
        verification = current.context.get("verification") or {}
        if not (verification.get("ok")
                and verification.get("ir_hash") == current.ir_hash):
            raise ValueError("verification_required: 需要绑定当前规则的验证证据")
        parsed = parse_design_ir(current.ir)
        if not isinstance(parsed, ComposedRulesIR):
            raise ValueError("publish_requires_composed_ir: 仅组合规则可注册新版本")
        # Idempotency across a crash between the artifact write and this commit:
        # if these exact rules already have a version, reuse it instead of
        # registering a second one.
        existing = store.find_artifact_by_ir(current.game_id, current.ir_hash)
        if existing is not None:
            summary = _artifact_summary(existing)
            idempotent = True
        else:
            version = payload.version or store.next_version(current.game_id)
            title = payload.title or _design_title(current)
            artifact = store.verify_and_register(
                parsed, game_id=current.game_id, version=version, title=title,
                approval_ir_hash=current.ir_hash)
            summary = _artifact_summary(artifact.as_dict())
            idempotent = False
        updated = designs.commit(session_id, payload.expected_revision
                                 if payload.expected_revision is not None
                                 else current.revision,
                                 request_id=payload.request_id, event="published",
                                 status="finalized", context={"published": summary})
        return {"published": True, "idempotent": idempotent, "artifact": summary,
                "session": updated.as_dict()}

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
                                     {"max_decisions": payload.max_steps},
                                     expected_revision=payload.expected_revision)
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
        return store.snapshot(store.create(payload.game_id, payload.seed, payload.version))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        return store.snapshot(store.get(session_id))

    @app.post("/api/sessions/{session_id}/actions")
    def act_generic(session_id: str, payload: ActionRequest):
        """The generic, descriptor-driven action endpoint (M5-2)."""
        body = store.act_generic(session_id, payload.action_id, payload.input_values,
                                 payload.revision, request_id=payload.request_id)
        body["action_id"] = payload.action_id
        return body

    @app.get("/api/sessions/{session_id}/events")
    def session_events(session_id: str, after: int = 0, viewer: str = "player-1",
                       limit: int = 200):
        """A bounded, cursor-based incremental event read (M5-2)."""
        return store.events(session_id, after=after, viewer=viewer, limit=limit)

    @app.post("/api/sessions/{session_id}/actions/{action}")
    def act(session_id: str, action: str, payload: ActionInput):
        # ADR-0007 adapter: the legacy field-shaped payload routes through the
        # same generic service, so there is one implementation behind both paths.
        input_values = {"card_index": payload.card_index,
                        "expression": payload.expression,
                        "declared_suit": payload.declared_suit,
                        "amount": payload.amount}
        return store.act_generic(session_id, action, input_values, payload.revision,
                                 request_id=payload.request_id)

    if DIST.is_dir():
        app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
    return app


#: The fields of an artifact that are cheap to send in a list or a summary.
_ARTIFACT_SUMMARY_KEYS = ("schema_version", "game_id", "version", "title",
                          "generation_source", "plan_hash", "ir_hash",
                          "compiler_version", "registry_contract_hash",
                          "verification_id", "approval_ir_hash")


def _artifact_summary(artifact: dict) -> dict:
    """The metadata of a stored artifact, without the plan or the rule body."""
    return {key: artifact.get(key) for key in _ARTIFACT_SUMMARY_KEYS}


def _design_title(session) -> str:
    """The user-facing title of a design, falling back to its game id."""
    ir = session.ir or {}
    meta = ir.get("meta") if isinstance(ir, dict) else None
    if isinstance(meta, dict) and meta.get("title"):
        return str(meta["title"])
    return str(session.game_id)


app = create_app()
