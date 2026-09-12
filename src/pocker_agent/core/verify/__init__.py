"""G2/M3: host-owned verification and the composed-rules publish service.

Registration can no longer be authorised by a caller-supplied ``{ok: true}``:
:func:`verify_plan` runs the host's formal strategies, :class:`VerificationResult`
records what was actually checked, and :func:`publish_composed` turns a verified
compiled plan into an immutable :class:`GameArtifact`.
"""
from .service import (
    VERIFICATION_SEEDS,
    VERIFICATION_STRATEGIES,
    default_invariants,
    publish_composed,
    verify_composed,
    verify_plan,
)

__all__ = [
    "VERIFICATION_SEEDS",
    "VERIFICATION_STRATEGIES",
    "default_invariants",
    "publish_composed",
    "verify_composed",
    "verify_plan",
]
