"""G2/M3: host-owned verification and the composed-rules publish service.

Registration can no longer be authorised by a caller-supplied ``{ok: true}``:
:func:`verify_plan` runs the host's formal strategies, :func:`verify_composed`
adds an independent IR-derived contract check, and :func:`publish_composed`
turns a verified compiled plan into an immutable
:class:`~pocker_agent.core.artifacts.GameArtifact`.
"""
from .contract_check import ClauseCheck, ContractReport, contract_check
from .service import (
    VERIFICATION_SEEDS,
    VERIFICATION_STRATEGIES,
    default_invariants,
    publish_composed,
    verify_composed,
    verify_plan,
)
from .trace import GameTrace, Observation, compare_traces, record_trace, replay_trace

__all__ = [
    "VERIFICATION_SEEDS",
    "VERIFICATION_STRATEGIES",
    "ClauseCheck",
    "ContractReport",
    "GameTrace",
    "Observation",
    "compare_traces",
    "contract_check",
    "default_invariants",
    "publish_composed",
    "record_trace",
    "replay_trace",
    "verify_composed",
    "verify_plan",
]
