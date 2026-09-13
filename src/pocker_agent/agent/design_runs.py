"""M5-1: durable, bounded records of one design turn.

A design *turn* is a long, model-driven operation. The session itself holds only
the current state; a *run* records one attempt at advancing it -- the stage it
reached, the observations the host tools returned and the budget it spent -- so a
client can poll a run instead of holding one long request, and so a process
restart does not lose the evidence of what the model tried.

A run is a pure trace, never an authority on the design: it stores no IR of its
own and has no path to registration. The design store remains the only writer of
the session (ADR-0014/0015).

Like the session store, every read returns a deep copy and every write is one
transaction. The number of runs a session keeps and the number of observations a
run keeps are both bounded, so a long-lived design cannot grow the database
without limit.
"""
from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..storage import connect

#: How many runs one session keeps; the oldest are pruned on insert.
MAX_RUNS_PER_SESSION = 50
#: How many observations one run keeps; the newest survive.
MAX_RUN_OBSERVATIONS = 64
MAX_RUN_MESSAGE = 4000

#: ``running`` while the turn is in flight, then a terminal state. ``kind`` keeps
#: the finer design result (question / finalized / unsupported / error / ...).
RUN_STATUSES = ("running", "completed", "failed")

_COMPLETED_KINDS = ("finalized", "question", "unsupported")


def run_status_for(kind: str | None) -> str:
    """Map a ``DesignResult.kind`` to a terminal run status.

    A question and an honest "unsupported" are successful turns; an error or an
    exhausted budget is not. ``None`` (still running) is reported as ``running``.
    """
    if kind is None:
        return "running"
    return "completed" if kind in _COMPLETED_KINDS else "failed"


@dataclass
class DesignRun:
    """One bounded attempt to advance a design session. Returned by value."""

    run_id: str
    session_id: str
    seq: int
    message: str = ""
    status: str = "running"
    kind: str | None = None
    revision: int = 0
    budget: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    used: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    artifact: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "session_id": self.session_id, "seq": self.seq,
                "message": self.message, "status": self.status, "kind": self.kind,
                "revision": self.revision, "budget": deepcopy(self.budget),
                "attempts": self.attempts, "used": deepcopy(self.used),
                "observations": deepcopy(self.observations),
                "artifact": deepcopy(self.artifact),
                "verification": deepcopy(self.verification), "error": self.error}

    def summary(self) -> dict[str, Any]:
        """The list view: no observation bodies, only their count."""
        return {"run_id": self.run_id, "session_id": self.session_id, "seq": self.seq,
                "message": self.message[:160], "status": self.status, "kind": self.kind,
                "revision": self.revision, "attempts": self.attempts,
                "used": deepcopy(self.used), "observations": len(self.observations),
                "artifact": deepcopy(self.artifact), "error": self.error}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesignRun:
        return cls(run_id=data["run_id"], session_id=data["session_id"],
                   seq=int(data.get("seq", 0)), message=data.get("message", ""),
                   status=data.get("status", "running"), kind=data.get("kind"),
                   revision=int(data.get("revision", 0)),
                   budget=deepcopy(data.get("budget", {})),
                   attempts=int(data.get("attempts", 0)),
                   used=deepcopy(data.get("used", {})),
                   observations=deepcopy(data.get("observations", [])),
                   artifact=deepcopy(data.get("artifact")),
                   verification=deepcopy(data.get("verification")),
                   error=data.get("error"))


class DesignRunStore:
    """Persistence for :class:`DesignRun`, keyed by run id and session id."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.runs: dict[str, DesignRun] = {}
        self.lock = threading.RLock()
        if path:
            with connect(path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS core_design_runs "
                           "(run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
                           "seq INTEGER NOT NULL, payload TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_core_design_runs_session "
                           "ON core_design_runs (session_id, seq)")

    # ------------------------------------------------------------- storage
    def _row(self, db: Any, run_id: str) -> DesignRun:
        row = db.execute("SELECT payload FROM core_design_runs WHERE run_id=?",
                         (run_id,)).fetchone()
        if not row:
            raise KeyError("design_run_not_found")
        return DesignRun.from_dict(json.loads(row[0]))

    def _write(self, db: Any, run: DesignRun) -> None:
        db.execute("INSERT OR REPLACE INTO core_design_runs VALUES (?, ?, ?, ?)",
                   (run.run_id, run.session_id, run.seq,
                    json.dumps(run.as_dict(), ensure_ascii=False)))

    def _next_seq(self, db: Any) -> int:
        row = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM core_design_runs").fetchone()
        return int(row[0])

    def _prune(self, db: Any, session_id: str) -> None:
        rows = db.execute(
            "SELECT run_id FROM core_design_runs WHERE session_id=? ORDER BY seq DESC",
            (session_id,)).fetchall()
        for (stale,) in rows[MAX_RUNS_PER_SESSION:]:
            db.execute("DELETE FROM core_design_runs WHERE run_id=?", (stale,))

    def _next_seq_mem(self) -> int:
        return 1 + max((run.seq for run in self.runs.values()), default=0)

    def _prune_mem(self, session_id: str) -> None:
        mine = sorted((run for run in self.runs.values() if run.session_id == session_id),
                      key=lambda run: run.seq, reverse=True)
        for stale in mine[MAX_RUNS_PER_SESSION:]:
            self.runs.pop(stale.run_id, None)

    # ------------------------------------------------------------ lifecycle
    def start(self, session_id: str, message: str, *, revision: int = 0,
              budget: dict[str, Any] | None = None) -> DesignRun:
        """Open a run before the model is asked anything, so it is pollable."""
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    seq = self._next_seq(db)
                    run = DesignRun(uuid.uuid4().hex, session_id, seq,
                                    str(message)[:MAX_RUN_MESSAGE], revision=revision,
                                    budget=deepcopy(budget or {}))
                    self._write(db, run)
                    self._prune(db, session_id)
            else:
                run = DesignRun(uuid.uuid4().hex, session_id, self._next_seq_mem(),
                                str(message)[:MAX_RUN_MESSAGE], revision=revision,
                                budget=deepcopy(budget or {}))
                self.runs[run.run_id] = deepcopy(run)
                self._prune_mem(session_id)
            return deepcopy(run)

    def finish(self, run_id: str, *, status: str, kind: str | None = None,
               revision: int | None = None, attempts: int | None = None,
               used: dict[str, Any] | None = None,
               observations: list[dict[str, Any]] | None = None,
               artifact: dict[str, Any] | None = None,
               verification: dict[str, Any] | None = None,
               error: str | None = None) -> DesignRun:
        """Close a run with its terminal state and bounded evidence."""
        if status not in RUN_STATUSES:
            raise ValueError(f"design_run_status_unknown:{status}")
        with self.lock:
            run = self.get(run_id)
            run.status = status
            run.kind = kind
            if revision is not None:
                run.revision = int(revision)
            if attempts is not None:
                run.attempts = int(attempts)
            if used is not None:
                run.used = deepcopy(used)
            if observations is not None:
                run.observations = deepcopy(observations[-MAX_RUN_OBSERVATIONS:])
            run.artifact = deepcopy(artifact)
            run.verification = deepcopy(verification)
            run.error = error
            if self.path:
                with connect(self.path) as db:
                    self._write(db, run)
            else:
                self.runs[run_id] = deepcopy(run)
            return deepcopy(run)

    def get(self, run_id: str) -> DesignRun:
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    return self._row(db, run_id)
            run = self.runs.get(run_id)
            if run is None:
                raise KeyError("design_run_not_found")
            return deepcopy(run)

    def list_runs(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    rows = db.execute(
                        "SELECT payload FROM core_design_runs WHERE session_id=? "
                        "ORDER BY seq DESC LIMIT ?", (session_id, int(limit))).fetchall()
                runs = [DesignRun.from_dict(json.loads(row[0])) for row in rows]
            else:
                runs = sorted((deepcopy(run) for run in self.runs.values()
                               if run.session_id == session_id),
                              key=lambda run: run.seq, reverse=True)[:int(limit)]
        return [run.summary() for run in runs]

    def latest(self, session_id: str) -> DesignRun | None:
        with self.lock:
            if self.path:
                with connect(self.path) as db:
                    row = db.execute(
                        "SELECT payload FROM core_design_runs WHERE session_id=? "
                        "ORDER BY seq DESC LIMIT 1", (session_id,)).fetchone()
                return DesignRun.from_dict(json.loads(row[0])) if row else None
            mine = [run for run in self.runs.values() if run.session_id == session_id]
            return deepcopy(max(mine, key=lambda run: run.seq)) if mine else None
