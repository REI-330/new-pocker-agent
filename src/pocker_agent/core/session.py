"""Playable sessions over the core interpreter.

``SessionStore`` is the only way to reach a running game. It refuses to create
a session for a game whose playtest gate has not passed, keeps a monotonic
``revision`` for optimistic concurrency, and persists the interpreter so a
session survives a restart without re-running the model (there is no model in
this path at all).

Games come from two places, both gated:
- host-compiled reference games (``reference.py``);
- agent-composed plans, registered only after the loop's ``finalize`` passed
  ``playtest``.
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
from .plan import GamePlan, plan_fingerprint
from .policy import run_bots
from .reference import REFERENCE_GAMES, build_plan, ensure_playtested, list_reference_games
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
        self.plans: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        if path:
            with connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS core_sessions "
                           "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS core_agent_plans "
                           "(game_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    # ------------------------------------------------------- plan registry
    def register_plan(self, game_id: str, plan: dict[str, Any], playtest_report: dict[str, Any],
                      title: str = "") -> None:
        """Register an agent-composed plan. Only a passing playtest may call this.

        Two rules keep a registration from rewriting history:
        * the built-in ids are reserved, so an agent game can never shadow a
          reference game (which would also collide in ``/api/games``);
        * an existing agent game keeps its plan unless the caller registers the
          *same* plan again -- a silent replacement would leave running sessions
          executing one graph while the registry described another.
        """
        if not playtest_report.get("ok"):
            raise ValueError("plan_not_playtested:" + game_id)
        if game_id in REFERENCE_GAMES:
            raise ValueError(f"game_id_reserved:{game_id}")
        validated = GamePlan.model_validate(plan)
        fingerprint = plan_fingerprint(validated)
        payload = {"plan": plan, "playtest": playtest_report,
                   "title": title or plan.get("game_kind", game_id),
                   "fingerprint": fingerprint}
        existing = self._stored_plans().get(game_id)
        if existing is not None:
            # Rows written before fingerprints existed are compared by content.
            previous = existing.get("fingerprint") or plan_fingerprint(existing["plan"])
            if previous != fingerprint:
                raise ValueError(f"plan_already_registered:{game_id}")
        if self.path:
            with connect(self.path) as db:
                db.execute("INSERT OR REPLACE INTO core_agent_plans VALUES (?, ?)",
                           (game_id, json.dumps(payload)))
        else:
            self.plans[game_id] = payload

    def _stored_plans(self) -> dict[str, dict[str, Any]]:
        if not self.path:
            return dict(self.plans)
        with connect(self.path) as db:
            rows = db.execute("SELECT game_id, payload FROM core_agent_plans").fetchall()
        return {game_id: json.loads(payload) for game_id, payload in rows}

    def list_games(self) -> list[dict[str, Any]]:
        games = list_reference_games()
        seen = {game["id"] for game in games}
        for game_id, payload in sorted(self._stored_plans().items()):
            if game_id in seen:                       # reserved ids cannot shadow
                continue
            games.append({"id": game_id, "title": payload.get("title", game_id),
                          "kind": payload["plan"].get("game_kind", "unknown"),
                          "playtest": payload["playtest"], "source": "agent_compose"})
        return games

    # ------------------------------------------------------------- lifecycle
    def _save(self, session: Session) -> None:
        payload = {"game_id": session.game_id, "revision": session.revision,
                   "seed": session.seed, "engine": session.interpreter.serialize(),
                   "plan_fingerprint": plan_fingerprint(session.plan)}
        if self.path:
            with connect(self.path) as db:
                db.execute("INSERT OR REPLACE INTO core_sessions VALUES (?, ?)",
                           (session.id, json.dumps(payload)))
        else:
            self.sessions[session.id] = session

    def _resolve_plan(self, game_id: str) -> GamePlan:
        stored = self._stored_plans().get(game_id)
        if stored and game_id not in REFERENCE_GAMES:
            return GamePlan.model_validate(stored["plan"])
        report = ensure_playtested(game_id)
        if not report.ok:
            raise ValueError("game_not_playtested:" + "; ".join(report.failures))
        return build_plan(game_id)

    def create(self, game_id: str, seed: int | None = None) -> Session:
        plan = self._resolve_plan(game_id)
        seed = secrets.randbelow(2**31) if seed is None else int(seed)
        interpreter = Interpreter(plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter)
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
            plan = self._resolve_plan(data["game_id"])
            recorded = data.get("plan_fingerprint")
            if recorded is not None and recorded != plan_fingerprint(plan):
                raise ValueError("plan_changed: 玩法版本已变化，这一局无法继续")
            return Session(session_id, data["game_id"], plan,
                           Interpreter.restore(data["engine"], core_registry()),
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
            run_bots(session.interpreter)
            session.revision += 1
            self._save(session)
            return {"event": event,
                    "new_events": session.interpreter.events[start:],
                    "state": self.snapshot(session)}

    def snapshot(self, session: Session, viewer: str = "player-1") -> dict[str, Any]:
        view = session.interpreter.view(viewer)
        view.pop("seed", None)
        return {**view, "session_id": session.id, "game_id": session.game_id,
                "revision": session.revision}
