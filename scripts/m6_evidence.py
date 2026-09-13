"""Independent evidence evaluator for M6 blind cases (ADR-0020).

A blind case carries its own expectations. This module decides whether a
registered artifact and a played trace actually satisfy them; ``finalized`` and
``finished`` alone are never accepted as evidence.

It is pure and name-agnostic: it checks structure (player/shared zones,
visibility, terminal thresholds, scoring) rather than zone or action names the
model happens to pick, and it checks the runtime trace against the frozen
independent expectation (action count, terminal reach, hidden projection).
"""
from __future__ import annotations

from typing import Any

Check = dict[str, Any]


def _check(checks: list[Check], name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})


def _player_zones(rules: dict) -> list[dict]:
    return [zone for zone in rules.get("zones") or [] if zone.get("scope") == "player"]


def _shared_zones(rules: dict) -> list[dict]:
    return [zone for zone in rules.get("zones") or [] if zone.get("scope", "shared") == "shared"]


def evaluate(case: dict, *, artifact: dict, rules: dict | None, plan: dict | None,
             verification: dict | None, initial_state: dict, final_state: dict,
             actions: list[dict], events: list[dict]) -> dict:
    """Return ``{"ok", "checks", "failure_class"}`` for one blind case."""
    evidence = case.get("evidence") or {}
    checks: list[Check] = []
    rules = rules or {}
    verification = verification or {}
    terminal = rules.get("terminal") or {}

    # ---------------------------------------------------------- provenance
    _check(checks, "generation_source",
           artifact.get("generation_source") == "composed_rules",
           artifact.get("generation_source"))
    _check(checks, "verification_ok", verification.get("ok") is True,
           verification.get("ok"))
    _check(checks, "hash_binding",
           bool(artifact.get("ir_hash"))
           and artifact.get("ir_hash") == verification.get("ir_hash")
           and artifact.get("plan_hash") == verification.get("plan_hash")
           and artifact.get("approval_ir_hash") == artifact.get("ir_hash"),
           f"ir={bool(artifact.get('ir_hash'))} "
           f"ir_match={artifact.get('ir_hash') == verification.get('ir_hash')} "
           f"plan_match={artifact.get('plan_hash') == verification.get('plan_hash')} "
           f"approval={artifact.get('approval_ir_hash') == artifact.get('ir_hash')}")
    _check(checks, "composed_ir", rules.get("kind") == "composed", rules.get("kind"))
    _check(checks, "plan_present", isinstance(plan, dict) and bool(plan.get("nodes")),
           type(plan).__name__)

    # -------------------------------------------------------------- shape
    players = (rules.get("players") or {}).get("count")
    expected_players = evidence.get("players")
    if expected_players is not None:
        _check(checks, "players", players == expected_players, f"{players}!={expected_players}")
    _check(checks, "player_zones", len(_player_zones(rules)) >= 1,
           len(_player_zones(rules)))
    min_shared = evidence.get("public_zones_min", 1)
    _check(checks, "shared_zones", len(_shared_zones(rules)) >= min_shared,
           len(_shared_zones(rules)))
    min_scoring = evidence.get("scoring_rules_min", 0)
    _check(checks, "scoring_rules", len(rules.get("scoring") or []) >= min_scoring,
           len(rules.get("scoring") or []))

    hidden = bool(evidence.get("hidden"))
    player_zones = _player_zones(rules)
    if player_zones:
        if hidden:
            visible = [zone.get("visibility") for zone in player_zones]
            _check(checks, "hidden_visibility",
                   all(item in ("owner_only", "hidden") for item in visible), visible)
        else:
            visible = [zone.get("visibility") for zone in player_zones]
            _check(checks, "public_visibility",
                   all(item == "public" for item in visible), visible)

    # ----------------------------------------------------------- terminal
    kind = evidence.get("terminal")
    if kind == "highest_score":
        _check(checks, "terminal.winner", terminal.get("winner") == "highest_score",
               terminal.get("winner"))
        if evidence.get("max_rounds") is not None:
            value = terminal.get("max_rounds")
            _check(checks, "terminal.max_rounds",
                   value == evidence["max_rounds"],
                   f"{value}!={evidence['max_rounds']}")
    elif kind == "score_reaches":
        target = evidence.get("score_reaches")
        value = terminal.get("score_reaches")
        _check(checks, "terminal.score_reaches",
               isinstance(value, int) and (target is None or value == target),
               f"{value}!={target}")
    elif kind == "actor_action_budget":
        budget = evidence.get("max_actor_actions")
        value = terminal.get("max_actor_actions")
        _check(checks, "terminal.max_actor_actions",
               isinstance(value, int) and (budget is None or value == budget),
               f"{value}!={budget}")
    elif kind == "max_rounds":
        rounds = evidence.get("max_rounds")
        value = terminal.get("max_rounds")
        _check(checks, "terminal.max_rounds",
               isinstance(value, int) and (rounds is None or value == rounds),
               f"{value}!={rounds}")
    else:
        _check(checks, "terminal.kind", False, kind)

    # -------------------------------------------------------------- trace
    action_count = int(final_state.get("action_count") or 0)
    scores = final_state.get("scores") or []
    _check(checks, "finished", final_state.get("finished") is True, final_state.get("finished"))
    _check(checks, "min_actions", action_count >= int(evidence.get("min_actions", 1)),
           f"{action_count}<{evidence.get('min_actions', 1)}")
    if kind == "score_reaches":
        _check(checks, "reached.score", max(scores or [0]) >= int(evidence["score_reaches"]),
               f"{scores}<{evidence['score_reaches']}")
    elif kind == "actor_action_budget":
        _check(checks, "reached.actions",
               action_count >= int(evidence["max_actor_actions"]),
               f"{action_count}<{evidence['max_actor_actions']}")
    elif kind == "max_rounds":
        _check(checks, "reached.rounds",
               int(final_state.get("round") or 0) >= int(evidence["max_rounds"]),
               final_state.get("round"))
    elif kind == "highest_score" and evidence.get("max_rounds"):
        _check(checks, "reached.rounds",
               int(final_state.get("round") or 0) >= int(evidence["max_rounds"]),
               final_state.get("round"))

    # ----------------------------------------------- hidden projection
    if hidden:
        opponent = (initial_state.get("players") or [{}, {}])[1]
        _check(checks, "hidden_projection",
               not opponent.get("hand") and int(opponent.get("hidden_count") or 0) > 0,
               f"hand={len(opponent.get('hand') or [])} hidden={opponent.get('hidden_count')}")
        if evidence.get("hidden_count") is not None:
            _check(checks, "hidden_count",
                   int(opponent.get("hidden_count") or 0) == int(evidence["hidden_count"]),
                   f"{opponent.get('hidden_count')}!={evidence['hidden_count']}")

    ok = all(item["ok"] for item in checks)
    return {"ok": ok, "checks": checks,
            "failure_class": None if ok else _failure_class(checks)}


def _failure_class(checks: list[Check]) -> str:
    """A host-side reason for an evidence mismatch, for the report."""
    failed = {item["name"] for item in checks if not item["ok"]}
    if failed & {"generation_source", "composed_ir", "verification_ok", "hash_binding",
                 "plan_present"}:
        return "validation"
    if failed & {"players", "player_zones", "shared_zones", "scoring_rules",
                 "hidden_visibility", "public_visibility", "terminal.winner",
                 "terminal.score_reaches", "terminal.max_actor_actions",
                 "terminal.max_rounds", "terminal.kind", "reached.actions",
                 "reached.rounds", "reached.score"}:
        return "composition"
    return "UI"
