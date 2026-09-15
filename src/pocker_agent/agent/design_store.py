"""M4-1: durable design sessions with optimistic concurrency.

A ``DesignSession`` is the state of one natural-language game-design
conversation. It deliberately holds only the *design process*: the raw
description, the current normalised IR, the latest diagnosis, a traceable
history and a status. It is not a playable artifact -- a runnable game still
comes only from ``publish_composed`` (ADR-0008), and this store has no path to
registration.

Concurrency contract:

* every write names the revision it expects;
* a mismatch is refused with ``stale_revision`` and the stored row is untouched;
* a failed write (invalid IR, unknown status, malformed diagnosis) leaves the
  revision, IR and diagnosis exactly as they were, because all mutation happens
  on a copy and only a fully-validated session is written, in one transaction;
* reads return deep copies, so a caller can never mutate stored state;
* a repeated ``request_id`` is idempotent: it returns the recorded result rather
  than applying the change twice.

State is kept in the same SQLite database as the playable sessions, so a process
restart restores every design session.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..storage import connect

#: The design lifecycle (ADR-0017). ``awaiting_confirmation`` is the state after
#: a candidate is frozen or the user has confirmed it; ``registered`` means an
#: immutable version exists. ``failed`` is a verification/unsupported stop that a
#: later ``propose_ir``/``patch_ir`` can repair back to ``draft``.
DESIGN_STATUSES = ("draft", "diagnosed", "compiled", "verified",
                   "awaiting_confirmation", "registered", "failed")

#: The fields a caller may change through :meth:`DesignStore.commit`.
CHANGE_KEYS = ("description", "ir", "diagnosis", "status", "context")

MAX_HISTORY = 256
MAX_PROCESSED_REQUESTS = 16
MAX_DESCRIPTION = 8000
#: Bounds on the design ``context`` map. Every list-valued key is trimmed from
#: the front, so the *most recent* turns/tool results survive a long session.
MAX_CONTEXT_ITEMS = 64
MAX_CONTEXT_CHAT = 60
MAX_CONTEXT_KEYS = 32
MAX_CONTEXT_DEPTH = 8
MAX_CONTEXT_TEXT = 8000
CONTEXT_LIST_KEYS = ("chat", "observations", "questions", "macros", "requirements")
#: Host-produced evidence whose full structure is needed to re-validate or replay.
CONTEXT_EVIDENCE_KEYS = ("verification", "compiled", "artifact", "published",
                         "confirmation", "verify_requests")


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def design_ir_hash(payload: dict[str, Any]) -> str:
    """Content identity of a normalised design IR (not a verification credential)."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def _bounded_value(value: Any, depth: int = 0) -> Any:
    """A finite copy of an arbitrary JSON value: capped depth, size and width.

    Applied to the free-form parts of the design context and to run observations,
    so neither can grow without bound or carry an arbitrarily deep structure.
    Host-produced evidence keys bypass this (see :data:`CONTEXT_EVIDENCE_KEYS`).
    """
    if depth >= MAX_CONTEXT_DEPTH:
        return "<truncated>"
    if isinstance(value, str):
        return value[:MAX_CONTEXT_TEXT]
    if isinstance(value, dict):
        items = list(value.items())[:MAX_CONTEXT_ITEMS]
        return {str(key)[:128]: _bounded_value(item, depth + 1) for key, item in items}
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth + 1) for item in list(value)[:MAX_CONTEXT_ITEMS]]
    return value


def _bounded_context(context: Any) -> dict[str, Any]:
    """A finite copy of the design context, so a long session cannot grow forever."""
    if not isinstance(context, dict):
        raise ValueError("design_context_must_be_object")
    bounded: dict[str, Any] = {}
    for key, value in context.items():
        if key in CONTEXT_LIST_KEYS:
            items = list(value) if isinstance(value, (list, tuple)) else []
            limit = MAX_CONTEXT_CHAT if key == "chat" else MAX_CONTEXT_ITEMS
            bounded[key] = [_bounded_value(item) for item in items[-limit:]]
        elif key in CONTEXT_EVIDENCE_KEYS:
            bounded[key] = deepcopy(value)
        else:
            bounded[key] = _bounded_value(value)
    if len(bounded) > MAX_CONTEXT_KEYS:
        # ``{**old, **new}`` keeps insertion order, so the newest keys are at the end.
        bounded = dict(list(bounded.items())[-MAX_CONTEXT_KEYS:])
    return bounded


def _normalize_ir(ir: Any) -> dict[str, Any]:
    """Parse and normalise a design IR, or raise ``invalid_ir``.

    Normalising on write means a stored session always holds a canonical IR, and
    an unparseable one is refused before anything is written.
    """
    from ..core.game_rules import normalize_rules

    if isinstance(ir, BaseModel):
        payload: Any = ir.model_dump(mode="json")
    elif isinstance(ir, dict):
        payload = deepcopy(ir)
    else:
        raise ValueError("design_ir_must_be_object")
    try:
        parsed = normalize_rules(payload)
    except Exception as error:                      # pydantic ValidationError
        raise ValueError(f"invalid_ir:{str(error).splitlines()[0]}") from error
    return parsed.model_dump(mode="json")


@dataclass
class DesignSession:
    """One durable design conversation. Returned by value, never by reference."""

    session_id: str
    game_id: str
    description: str = ""
    ir: dict[str, Any] | None = None
    ir_hash: str | None = None
    diagnosis: dict[str, Any] | None = None
    revision: int = 0
    status: str = "draft"
    context: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    processed: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """A deep copy, so a caller cannot mutate the stored session through it."""
        return {
            "session_id": self.session_id, "game_id": self.game_id,
            "description": self.description, "ir": deepcopy(self.ir),
            "ir_hash": self.ir_hash, "diagnosis": deepcopy(self.diagnosis),
            "revision": self.revision, "status": self.status,
            "context": deepcopy(self.context),
            "history": deepcopy(self.history), "processed": deepcopy(self.processed),
        }

    def snapshot(self) -> dict[str, Any]:
        """The session without its idempotency map (records one request result)."""
        payload = self.as_dict()
        payload["processed"] = {}
        return payload

    def summary(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "game_id": self.game_id,
                "revision": self.revision, "status": self.status,
                "ir_hash": self.ir_hash, "events": len(self.history),
                "messages": len(self.context.get("chat", []))}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesignSession:
        return cls(
            session_id=data["session_id"], game_id=data["game_id"],
            description=data.get("description", ""), ir=deepcopy(data.get("ir")),
            ir_hash=data.get("ir_hash"), diagnosis=deepcopy(data.get("diagnosis")),
            revision=int(data.get("revision", 0)), status=data.get("status", "draft"),
            context=deepcopy(data.get("context", {})),
            history=deepcopy(data.get("history", [])),
            processed=deepcopy(data.get("processed", {})))


class DesignStore:
    """Persistence and optimistic locking for :class:`DesignSession`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.sessions: dict[str, DesignSession] = {}
        self.lock = threading.RLock()
        if path:
            with connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS core_design_sessions "
                           "(session_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    # ------------------------------------------------------------- storage
    def _read(self, db: Any, session_id: str) -> DesignSession:
        row = db.execute("SELECT payload FROM core_design_sessions WHERE session_id=?",
                         (session_id,)).fetchone()
        if not row:
            raise KeyError("design_session_not_found")
        return DesignSession.from_dict(json.loads(row[0]))

    def _write(self, db: Any, session: DesignSession) -> None:
        db.execute("INSERT OR REPLACE INTO core_design_sessions VALUES (?, ?)",
                   (session.session_id, json.dumps(session.as_dict(), ensure_ascii=False)))

    def _load(self, session_id: str) -> DesignSession:
        if self.path:
            with connect(self.path) as db:
                return self._read(db, session_id)
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError("design_session_not_found")
        return deepcopy(session)

    # ------------------------------------------------------------ lifecycle
    def create(self, game_id: str, description: str = "", *,
               session_id: str | None = None) -> DesignSession:
        game_id = str(game_id).strip()
        if not game_id:
            raise ValueError("design_game_id_required")
        if len(game_id) > 64:
            raise ValueError("design_game_id_too_long")
        session = DesignSession(
            session_id=session_id or uuid.uuid4().hex, game_id=game_id,
            description=str(description)[:MAX_DESCRIPTION], revision=0, status="draft",
            history=[{"seq": 0, "event": "created", "revision": 0}])
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    self._write(db, session)
            else:
                self.sessions[session.session_id] = deepcopy(session)
        return deepcopy(session)

    def get(self, session_id: str) -> DesignSession:
        with self.lock:
            return self._load(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    rows = db.execute("SELECT payload FROM core_design_sessions").fetchall()
                items = [DesignSession.from_dict(json.loads(row[0])) for row in rows]
            else:
                items = [deepcopy(session) for session in self.sessions.values()]
        return [item.summary() for item in sorted(items, key=lambda s: s.session_id)]

    # --------------------------------------------------------------- commit
    @staticmethod
    def _prepare(current: DesignSession, expected_revision: int, request_id: str | None,
                 event: str, fingerprint: str,
                 changes: dict[str, Any]) -> tuple[DesignSession, bool]:
        """Validate and apply a change to a copy, or raise. Never touches ``current``."""
        if request_id:
            entry = current.processed.get(request_id)
            if entry is not None:
                if entry.get("fingerprint") != fingerprint:
                    raise ValueError("request_id_conflict: 相同 request_id 的请求内容不同")
                return DesignSession.from_dict(entry["session"]), False
        if current.revision != expected_revision:
            raise ValueError(
                f"stale_revision: 设计会话已更新（当前 {current.revision}，期望 {expected_revision}）")
        unknown = sorted(set(changes) - set(CHANGE_KEYS))
        if unknown:
            raise ValueError(f"design_change_unknown:{unknown[0]}")

        updated = deepcopy(current)
        if "description" in changes:
            updated.description = str(changes["description"])[:MAX_DESCRIPTION]
        if "ir" in changes:
            normalized = _normalize_ir(changes["ir"])
            normalized["game_id"] = current.game_id
            source = normalized.get("execution", {}).get("rules")
            if isinstance(source, dict) and "game_id" in source:
                source["game_id"] = current.game_id
            normalized = _normalize_ir(normalized)
            updated.ir = normalized
            updated.ir_hash = design_ir_hash(normalized)
        if "diagnosis" in changes:
            diagnosis = changes["diagnosis"]
            if diagnosis is not None and not isinstance(diagnosis, dict):
                raise ValueError("design_diagnosis_must_be_object")
            updated.diagnosis = deepcopy(diagnosis)
        if "status" in changes:
            if changes["status"] not in DESIGN_STATUSES:
                raise ValueError(f"design_status_unknown:{changes['status']}")
            updated.status = changes["status"]
        if "context" in changes:
            if not isinstance(changes["context"], dict):
                raise ValueError("design_context_must_be_object")
            # A partial update merges at the top level, so a caller can change one
            # key (``chat``/``compiled``) without re-sending the whole map.
            merged = {**updated.context, **deepcopy(changes["context"])}
            updated.context = _bounded_context(merged)

        updated.revision = current.revision + 1
        updated.history = [*updated.history, {"seq": len(updated.history), "event": event,
                                              "revision": updated.revision}][-MAX_HISTORY:]
        if request_id:
            updated.processed = dict(updated.processed)
            updated.processed[request_id] = {"fingerprint": fingerprint,
                                             "session": updated.snapshot()}
            for stale in list(updated.processed)[:-MAX_PROCESSED_REQUESTS]:
                updated.processed.pop(stale, None)
        return updated, True

    def commit(self, session_id: str, expected_revision: int, *, request_id: str | None = None,
               event: str = "updated", **changes: Any) -> DesignSession:
        """Apply ``changes`` at ``expected_revision`` atomically, or refuse.

        ``changes`` may contain ``description``, ``ir``, ``diagnosis`` and/or
        ``status``. On any failure the stored session is unchanged.
        """
        event = str(event)[:64]
        fingerprint = _fingerprint({"expected_revision": expected_revision,
                                    "request_id": request_id, "event": event,
                                    "changes": changes})
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    current = self._read(db, session_id)
                    updated, changed = self._prepare(current, expected_revision, request_id,
                                                     event, fingerprint, changes)
                    if changed:
                        self._write(db, updated)
                    return deepcopy(updated)
            current = self.sessions.get(session_id)
            if current is None:
                raise KeyError("design_session_not_found")
            updated, changed = self._prepare(deepcopy(current), expected_revision, request_id,
                                             event, fingerprint, changes)
            if changed:
                self.sessions[session_id] = deepcopy(updated)
            return deepcopy(updated)
