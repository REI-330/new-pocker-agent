from __future__ import annotations

import json
import secrets
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .engine import RuleEngine
from .executors import create_engine, restore_engine
from .family_engines import FamilyEngine
from .game_rules import PlayableRule
from .storage import connect


@dataclass
class RuntimeSession:
    id: str
    engine: RuleEngine | FamilyEngine
    revision: int = 0
    seed: int = 0


def run_bots(engine):
    for _ in range(10000):
        if engine.state.finished or engine.state.current_player == 0:
            return
        engine.step()
    raise RuntimeError("bot_step_limit: 电脑回合超过上限，请简化规则")


class RuntimeStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.sessions = {}
        self.lock = threading.RLock()
        if path:
            with connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def _save(self, session):
        if self.path:
            with connect(self.path) as db:
                db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session.id, json.dumps(
                    {"engine": session.engine.serialize(), "revision": session.revision, "seed": session.seed})))
        else:
            self.sessions[session.id] = session

    def create(self, rules: PlayableRule, seed: int | None = None):
        seed = secrets.randbelow(2**31) if seed is None else seed
        session = RuntimeSession(uuid.uuid4().hex, create_engine(rules, seed=seed), seed=seed)
        session.engine.setup()
        run_bots(session.engine)
        with self.lock:
            self._save(session)
        return session

    def get(self, session_id):
        if self.path:
            with connect(self.path) as db:
                row = db.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row:
                data = json.loads(row[0])
                return RuntimeSession(session_id, restore_engine(data["engine"]), data["revision"], data["seed"])
        elif session_id in self.sessions:
            return self.sessions[session_id]
        raise KeyError("runtime_session_not_found")

    def act(self, session_id, action, revision, card_index=0, *, expression="", declared_suit=""):
        with self.lock:
            session = self.get(session_id)
            if session.revision != revision:
                raise ValueError("stale_revision: 牌局已经更新，请刷新牌局")
            # Execute on a copy; rejected actions must not mutate even an in-memory session.
            session = RuntimeSession(session.id, restore_engine(session.engine.serialize()), session.revision, session.seed)
            start = len(session.engine.events)
            if hasattr(session.engine.rules, "kind"):
                event = session.engine.step(action, card_index, expression=expression, declared_suit=declared_suit)
            else:
                event = session.engine.step(action, card_index)
            run_bots(session.engine)
            session.revision += 1
            self._save(session)
            return {"event": event, "events": session.engine.events[start:], "state": snapshot(session)}


def snapshot(session):
    state = session.engine.state
    result = {
        "session_id": session.id, "revision": session.revision, "seed": session.seed,
        "phase": state.phase, "round": state.round_number, "max_rounds": session.engine.rules.max_rounds,
        "current_player": state.players[state.current_player].id, "human_player": "player-1",
        "finished": state.finished, "winners": state.winners, "finish_reason": state.finish_reason,
        "legal_actions": session.engine.legal_actions(),
        "players": [{"id": p.id, "hand": [c.as_dict() for c in p.hand], "score": p.score} for p in state.players],
        "table": [c.as_dict() for c in state.table], "deck_remaining": len(state.deck),
        "events": session.engine.events[-100:],
    }
    if hasattr(session.engine, "view"):
        result.update(session.engine.view())
        if result.get("kind") in {"blackjack", "shedding"} and not state.finished:
            # A seed would let a client reconstruct private hands and the remaining deck.
            result.pop("seed", None)
    return result


def export_package(rules):
    from .exporting import export_package as compile_export
    return compile_export(rules)
