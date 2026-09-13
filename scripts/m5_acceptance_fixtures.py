"""Fixtures for the M5 browser acceptance (ADR-0019).

Publishes the frozen G2 acceptance scenarios as real composed games and seeds
three *verified* design sessions, so the browser can exercise the full
``confirm -> publish -> play`` path without a model. The composed IRs are the
same frozen fixtures the backend tests use, imported rather than copied so the
two can never drift apart.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_g2_m2 import scenario_a_ir  # noqa: E402
from test_g2_m3 import dual_zone_ir  # noqa: E402
from test_g2_m3_action_sequence import two_action_ir  # noqa: E402
from test_g2_m3_draw_pass import draw_pass_ir  # noqa: E402
from test_g2_m3_scenario_c import book_collection_ir, scenario_c_ir  # noqa: E402

from pocker_agent.core.session import SessionStore  # noqa: E402

#: Composed games the browser renders. ``theta-draw`` is deliberately long so the
#: mid-game/restart/race sections have room to run.
PUBLISHED_GAMES = [
    ("alpha-pairs", scenario_a_ir),
    ("gamma-market", scenario_c_ir),
    ("delta-book", book_collection_ir),
    ("epsilon-dual", dual_zone_ir),
    ("eta-two", two_action_ir),
    ("theta-draw", lambda: draw_pass_ir(actions=24)),
]

#: Sessions the browser drives through confirm/publish. They are seeded in
#: ``verified`` state by the host gate, which needs no model.
DESIGN_SEEDS = [
    ("ui-alpha", scenario_a_ir),
    ("ui-gamma", scenario_c_ir),
    ("ui-eta", two_action_ir),
    ("ui-publish", two_action_ir),
    ("ui-race", scenario_c_ir),
]


def publish_all(database: Path) -> dict[str, dict[str, object]]:
    """Register every composed fixture as version 1 of its own game id."""
    store = SessionStore(database)
    published: dict[str, dict[str, object]] = {}
    for game_id, builder in PUBLISHED_GAMES:
        try:
            artifact = store.verify_and_register(builder(), game_id=game_id, version=1,
                                                 title=game_id)
            published[game_id] = {"ok": True, "version": artifact.version,
                                  "verification_id": artifact.verification_id}
        except Exception as error:  # reported to the caller
            published[game_id] = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    return published


def _request(base_url: str, path: str, method: str = "GET", body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(base_url + path, data=data, method=method,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw.startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        return error.code, (json.loads(raw) if raw.startswith(("{", "[")) else raw)


def seed_designs(base_url: str) -> dict[str, dict[str, str]]:
    """Create each design seed, write its IR and run the host gate to ``verified``."""
    designs: dict[str, dict[str, str]] = {}
    for game_id, builder in DESIGN_SEEDS:
        status, created = _request(base_url, "/api/designs", "POST",
                                   {"game_id": game_id,
                                    "description": f"{game_id} browser acceptance"})
        if status != 200:
            raise SystemExit(f"seed {game_id}: create failed {status} {created}")
        session_id = created["session_id"]
        status, updated = _request(base_url, f"/api/designs/{session_id}/update", "POST",
                                   {"expected_revision": 0, "ir": builder(),
                                    "status": "diagnosed", "event": "propose_ir"})
        if status != 200:
            raise SystemExit(f"seed {game_id}: update failed {status} {updated}")
        status, verified = _request(base_url, f"/api/designs/{session_id}/verify", "POST",
                                    {"expected_revision": updated["revision"]})
        if status != 200 or not verified.get("ok"):
            raise SystemExit(f"seed {game_id}: verify failed {status} {verified}")
        designs[game_id] = {"session_id": session_id,
                            "ir_hash": verified["session"]["ir_hash"]}
    return designs
