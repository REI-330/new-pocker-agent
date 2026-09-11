"""Playable sessions over the core interpreter.

``SessionStore`` is the only way to reach a running game. It refuses to create
a session for a game whose playtest gate has not passed, keeps a monotonic
``revision`` for optimistic concurrency, and persists the interpreter so a
session survives a restart without re-running the model (there is no model in
this path at all).
"""
from __future__ import annotations

import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage import connect
from .interpreter import Interpreter
from .plan import GamePlan
from .reference import build_plan, ensure_playtested
from .registry import core_registry


@dataclass
class Session:
    id: str
    game_id: str
    plan: GamePlan
    interpreter: Interpreter
    revision: int = 0
    seed: int = 0


class SessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        if path:
            with connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS core_sessions "
                           "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    # ------------------------------------------------------------- lifecycle
    def _save(self, session: Session) -> None:
        payload = {"game_id": session.game_id, "revision": session.revision,
                   "seed": session.seed, "engine": session.interpreter.serialize()}
        if self.path:
            with connect(self.path) as db:
                db.execute("INSERT OR REPLACE INTO core_sessions VALUES (?, ?)",
                           (session.id, json.dumps(payload)))
        else:
            self.sessions[session.id] = session

    def create(self, game_id: str, seed: int | None = None) -> Session:
        report = ensure_playtested(game_id)
        if not report.ok:
            raise ValueError("game_not_playtested:" + "; ".join(report.failures))
        seed = secrets.randbelow(2**31) if seed is None else int(seed)
        plan = build_plan(game_id)
        interpreter = Interpreter(plan, core_registry(), seed=seed)
        interpreter.setup()
        session = Session(uuid.uuid4().hex, game_id, plan, interpreter, revision=0, seed=seed)
        with self.lock:
            self._save(session)
        return session

    def get(self, session_id: str) -> Session:
        if self.path:
            with connect(self.path) as db:
                row = db.execute("SELECT payload FROM core_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise KeyError("session_not_found")
            data = json.loads(row[0])
            plan = build_plan(data["game_id"])
            interpreter = Interpreter.restore(data["engine"], core_registry())
            return Session(session_id, data["game_id"], plan, interpreter,
                           data["revision"], data["seed"])
        if session_id in self.sessions:
            return self.sessions[session_id]
        raise KeyError("session_not_found")

    def act(self, session_id: str, action: str, revision: int, **payload: Any) -> dict[str, Any]:
        with self.lock:
            session = self.get(session_id)
            if session.revision != revision:
                raise ValueError("stale_revision: 牌局已经更新，请刷新牌局")
            start = len(session.interpreter.events)
            event = session.interpreter.step(action, **payload)
            session.revision += 1
            self._save(session)
            return {"event": event,
                    "new_events": session.interpreter.events[start:],
                    "state": self.snapshot(session)}

    def snapshot(self, session: Session) -> dict[str, Any]:
        view = session.interpreter.view()
        view.pop("seed", None)
        return {**view, "session_id": session.id, "game_id": session.game_id,
                "revision": session.revision}
