"""Playable sessions over the core interpreter.

``SessionStore`` is the only way to reach a running game. It refuses to create
a session for a game whose playtest gate has not passed, keeps a monotonic
``revision`` for optimistic concurrency, and persists the interpreter so a
session survives a restart without re-running the model (there is no model in
this path at all).

Games come from three places, all gated:
- host-compiled reference games (``reference.py``);
- legacy agent plans registered from a host playtest report (``register_plan``);
- immutable, verified artifacts published by ``verify/`` (``register_artifact``).

An artifact is authorised only by a verification credential the host itself
recorded (ADR-0008): ``register_artifact`` recomputes the plan hash, looks the
cited ``verification_id`` up in the host table, and refuses a missing, stale or
failed one. A session then restores against the *version* it ran, never against
whatever now happens to sit under the same game id.
"""
from __future__ import annotations

import json
import secrets
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..storage import connect
from .artifacts import GameArtifact, VerificationResult
from .interpreter import Interpreter
from .plan import GamePlan, plan_fingerprint
from .policy import run_bots
from .reference import REFERENCE_GAMES, build_plan, ensure_playtested, list_reference_games
from .registry import core_registry
from .verify import VERIFICATION_SEEDS, VERIFICATION_STRATEGIES, publish_composed

# How many recent idempotency keys a session remembers. Enough for a retry
# window without letting a long game grow the persisted payload without bound.
MAX_PROCESSED_REQUESTS = 16


@dataclass
class Session:
    id: str
    game_id: str
    plan: GamePlan
    interpreter: Interpreter
    revision: int = 0
    seed: int = 0
    version: int | None = None
    processed: dict[str, dict[str, Any]] = field(default_factory=dict)


class SessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.sessions: dict[str, Session] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.artifacts: dict[tuple[str, int], dict[str, Any]] = {}
        self.verifications: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        if path:
            with connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS core_sessions "
                           "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS core_agent_plans "
                           "(game_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS core_artifacts "
                           "(game_id TEXT NOT NULL, version INTEGER NOT NULL, "
                           "payload TEXT NOT NULL, PRIMARY KEY (game_id, version))")
                db.execute("CREATE TABLE IF NOT EXISTS core_verifications "
                           "(verification_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    # ------------------------------------------------------- plan registry
    def register_plan(self, game_id: str, plan: dict[str, Any], playtest_report: dict[str, Any],
                      title: str = "") -> None:
        """Register an agent-composed plan from a host playtest report.

        Kept for the known-family agent path while the M4/M5 consumers migrate to
        ``register_artifact``. Two rules keep a registration from rewriting
        history: the built-in ids are reserved, and an existing agent game keeps
        its plan unless the caller registers the *same* plan again.
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

    # ------------------------------------------------ artifacts & credentials
    def record_verification(self, result: VerificationResult) -> None:
        """Persist a host-issued credential; registration can only cite these."""
        payload = result.as_dict()
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    db.execute("INSERT OR REPLACE INTO core_verifications VALUES (?, ?)",
                               (result.verification_id, json.dumps(payload)))
            else:
                self.verifications[result.verification_id] = payload

    def _stored_verifications(self) -> dict[str, dict[str, Any]]:
        if not self.path:
            return dict(self.verifications)
        with connect(self.path) as db:
            rows = db.execute("SELECT verification_id, payload FROM core_verifications").fetchall()
        return {verification_id: json.loads(payload) for verification_id, payload in rows}

    def register_artifact(self, artifact: GameArtifact) -> None:
        """Register an immutable artifact, authorised only by a recorded credential.

        This is the unbypassable gate: the plan hash is recomputed, the cited
        ``verification_id`` must exist in the host's own table, and its binding
        must match the artifact. A forged, missing, stale or failed credential is
        refused. An existing ``(game_id, version)`` keeps its content, so a new
        rule set must take a new version.
        """
        if artifact.game_id in REFERENCE_GAMES:
            raise ValueError(f"game_id_reserved:{artifact.game_id}")
        validated = GamePlan.model_validate(artifact.plan)
        if plan_fingerprint(validated) != artifact.plan_hash:
            raise ValueError("artifact_plan_hash_mismatch")
        stored = self._stored_verifications().get(artifact.verification_id)
        if stored is None:
            raise ValueError(f"verification_unknown:{artifact.verification_id}")
        result = VerificationResult.from_dict(stored)
        if not result.ok:
            raise ValueError(f"verification_failed:{artifact.verification_id}")
        if not artifact.matches(result):
            raise ValueError("verification_stale")
        payload = artifact.as_dict()
        with self.lock:
            existing = self._find_artifact(artifact.game_id, artifact.version)
            if existing is not None:
                if existing["plan_hash"] != artifact.plan_hash:
                    raise ValueError(
                        f"artifact_version_conflict:{artifact.game_id}@{artifact.version}")
                return
            if self.path:
                with connect(self.path) as db:
                    db.execute("INSERT OR REPLACE INTO core_artifacts VALUES (?, ?, ?)",
                               (artifact.game_id, artifact.version, json.dumps(payload)))
            else:
                self.artifacts[(artifact.game_id, artifact.version)] = payload

    def verify_and_register(self, ir: Any, *, game_id: str, version: int, title: str,
                            approval_ir_hash: str | None = None,
                            registry: Any = None,
                            strategies: Any = VERIFICATION_STRATEGIES,
                            seeds: Any = VERIFICATION_SEEDS, invariants: Any = (),
                            require_wait_coverage: bool = True) -> GameArtifact:
        """Run the publish service and, in order, record then register the result.

        The credential is written before the artifact that cites it, so a crash
        between the two leaves an unused credential, never a dangling one.
        """
        _, result, artifact = publish_composed(
            ir, game_id=game_id, version=version, title=title,
            registry=registry or core_registry(), approval_ir_hash=approval_ir_hash,
            strategies=strategies, seeds=seeds, invariants=invariants,
            require_wait_coverage=require_wait_coverage)
        self.record_verification(result)
        self.register_artifact(artifact)
        return artifact

    def _stored_artifacts(self) -> dict[tuple[str, int], dict[str, Any]]:
        if not self.path:
            return dict(self.artifacts)
        with connect(self.path) as db:
            rows = db.execute("SELECT game_id, version, payload FROM core_artifacts").fetchall()
        return {(game_id, version): json.loads(payload) for game_id, version, payload in rows}

    def _find_artifact(self, game_id: str, version: int | None) -> dict[str, Any] | None:
        artifacts = self._stored_artifacts()
        if version is not None:
            return artifacts.get((game_id, version))
        versions = [item for (gid, item) in artifacts if gid == game_id]
        return artifacts[(game_id, max(versions))] if versions else None

    def list_versions(self, game_id: str) -> list[dict[str, Any]]:
        return [payload for (gid, _), payload in sorted(self._stored_artifacts().items())
                if gid == game_id]

    def list_games(self) -> list[dict[str, Any]]:
        games = list_reference_games()
        seen = {game["id"] for game in games}
        artifacts = self._stored_artifacts()
        for game_id in sorted({gid for gid, _ in artifacts}):
            if game_id in seen:                       # reserved ids cannot shadow
                continue
            seen.add(game_id)
            payload = artifacts[(game_id, max(v for g, v in artifacts if g == game_id))]
            games.append({"id": game_id, "title": payload.get("title", game_id),
                          "kind": payload["plan"].get("game_kind", "unknown"),
                          "version": payload["version"],
                          "verification_id": payload["verification_id"],
                          "playtest": {"ok": True},
                          "source": payload["generation_source"]})
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
                   "seed": session.seed, "version": session.version,
                   "processed": session.processed,
                   "engine": session.interpreter.serialize(),
                   "plan_fingerprint": plan_fingerprint(session.plan)}
        if self.path:
            with connect(self.path) as db:
                db.execute("INSERT OR REPLACE INTO core_sessions VALUES (?, ?)",
                           (session.id, json.dumps(payload)))
        else:
            self.sessions[session.id] = session

    def _resolve_plan(self, game_id: str,
                      version: int | None = None) -> tuple[GamePlan, int | None]:
        artifact = self._find_artifact(game_id, version)
        if artifact is not None:
            return GamePlan.model_validate(artifact["plan"]), artifact["version"]
        if version is not None:
            raise ValueError(f"version_not_found:{game_id}@{version}")
        stored = self._stored_plans().get(game_id)
        if stored and game_id not in REFERENCE_GAMES:
            return GamePlan.model_validate(stored["plan"]), None
        report = ensure_playtested(game_id)
        if not report.ok:
            raise ValueError("game_not_playtested:" + "; ".join(report.failures))
        return build_plan(game_id), None

    def create(self, game_id: str, seed: int | None = None,
               version: int | None = None) -> Session:
        plan, resolved_version = self._resolve_plan(game_id, version)
        seed = secrets.randbelow(2**31) if seed is None else int(seed)
        interpreter = Interpreter(plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter)
        session = Session(uuid.uuid4().hex, game_id, plan, interpreter, revision=0,
                          seed=seed, version=resolved_version)
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
            plan, _ = self._resolve_plan(data["game_id"], data.get("version"))
            recorded = data.get("plan_fingerprint")
            if recorded is not None and recorded != plan_fingerprint(plan):
                raise ValueError("plan_changed: 玩法版本已变化，这一局无法继续")
            return Session(session_id, data["game_id"], plan,
                           Interpreter.restore(data["engine"], core_registry()),
                           data["revision"], data["seed"], data.get("version"),
                           data.get("processed", {}))
        if session_id in self.sessions:
            return self.sessions[session_id]
        raise KeyError("session_not_found")

    def act(self, session_id: str, action: str, revision: int,
            request_id: str | None = None, **payload: Any) -> dict[str, Any]:
        """One human action plus the bot batch, as a single atomic commit.

        If the human step or a bot step raises, the interpreter is rolled back to
        the committed state and ``revision`` is unchanged, so a bot failure never
        leaves a half-advanced game (M3 exit criterion 9). A repeated
        ``request_id`` returns the original response instead of applying the
        action twice.
        """
        with self.lock:
            session = self.get(session_id)
            if request_id and request_id in session.processed:
                return deepcopy(session.processed[request_id])
            if session.revision != revision:
                raise ValueError("stale_revision: 牌局已经更新，请刷新牌局")
            start = len(session.interpreter.events)
            snapshot = session.interpreter.serialize()
            try:
                event = session.interpreter.step(action, **payload)
                run_bots(session.interpreter)
            except Exception:
                session.interpreter = Interpreter.restore(snapshot, session.interpreter.registry)
                raise
            session.revision += 1
            response = {"event": event,
                        "new_events": session.interpreter.events[start:],
                        "state": self.snapshot(session)}
            if request_id:
                session.processed[request_id] = deepcopy(response)
                for stale in list(session.processed)[:-MAX_PROCESSED_REQUESTS]:
                    session.processed.pop(stale, None)
            self._save(session)
            return response

    def snapshot(self, session: Session, viewer: str = "player-1") -> dict[str, Any]:
        view = session.interpreter.view(viewer)
        view.pop("seed", None)
        return {**view, "session_id": session.id, "game_id": session.game_id,
                "revision": session.revision, "version": session.version}
