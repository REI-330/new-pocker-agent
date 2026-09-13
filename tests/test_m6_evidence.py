"""M6 evidence evaluator tests (ADR-0020).

The evaluator is the anti-false-positive gate: a case must satisfy its own
independent expectations (zones, visibility, terminal, action budget, hidden
projection, hash binding), not merely finish. These tests prove it rejects a
plausible-looking but wrong artifact.
"""
from __future__ import annotations

import sys
from pathlib import Path

from test_g2_m2 import scenario_a_ir
from test_g2_m3_scenario_b import scenario_b_ir

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from m6_evidence import evaluate  # noqa: E402

PLAN = {"nodes": {"start": {"kind": "setup"}}, "entries": ["start"]}


def _artifact(ir_hash="ir1", plan_hash="pl1"):
    return {"generation_source": "composed_rules", "ir_hash": ir_hash,
            "plan_hash": plan_hash, "approval_ir_hash": ir_hash}


def _verification(ir_hash="ir1", plan_hash="pl1", ok=True):
    return {"ok": ok, "ir_hash": ir_hash, "plan_hash": plan_hash}


def _hidden_initial(count=3):
    return {"players": [{"hand": [{"id": "H1"}], "hidden_count": 0},
                        {"hand": [], "hidden_count": count}]}


def _public_initial():
    return {"players": [{"hand": [{"id": "H1"}]}, {"hand": [{"id": "H2"}]}]}


CASE_A = {"id": "blind-a", "expect": "composed",
          "evidence": {"players": 2, "hidden": False, "public_zones_min": 1,
                       "scoring_rules_min": 1, "terminal": "highest_score",
                       "max_rounds": 3, "min_actions": 4}}
FINAL_A = {"finished": True, "action_count": 6, "round": 3, "scores": [2, 1],
           "winners": ["player-1"]}

CASE_HIDDEN = {"id": "blind-hidden", "expect": "composed",
               "evidence": {"players": 2, "hidden": True, "hidden_count": 3,
                            "public_zones_min": 1,
                            "terminal": "score_reaches", "score_reaches": 6,
                            "min_actions": 2}}
FINAL_HIDDEN = {"finished": True, "action_count": 5, "round": 2, "scores": [6, 2],
                "winners": ["player-1"]}


def _run(case, rules, artifact=None, verification=None, initial=None, final=None):
    return evaluate(case, artifact=artifact or _artifact(),
                    rules=rules, plan=PLAN,
                    verification=verification or _verification(),
                    initial_state=initial or _public_initial(),
                    final_state=final or FINAL_A, actions=[], events=[])


def test_a_matching_case_passes():
    result = _run(CASE_A, scenario_a_ir())
    assert result["ok"] is True, [c for c in result["checks"] if not c["ok"]]


def test_a_wrong_generation_source_fails():
    result = _run(CASE_A, scenario_a_ir(), artifact={**_artifact(),
                                                     "generation_source": "known_parameters"})
    assert result["ok"] is False
    assert result["failure_class"] == "validation"


def test_a_hash_mismatch_fails():
    result = _run(CASE_A, scenario_a_ir(),
                  artifact=_artifact(ir_hash="ir1", plan_hash="other"))
    assert result["ok"] is False
    assert any(check["name"] == "hash_binding" and not check["ok"] for check in result["checks"])


def test_a_failed_terminal_does_not_confirm_the_case():
    result = _run(CASE_A, scenario_a_ir(), final={**FINAL_A, "action_count": 2, "round": 1})
    assert result["ok"] is False
    assert any(check["name"] in ("reached.rounds", "min_actions") and not check["ok"]
               for check in result["checks"])


def test_an_action_budget_case_must_reach_its_budget():
    case = {"id": "b", "expect": "composed",
            "evidence": {"players": 2, "hidden": False, "public_zones_min": 1,
                         "terminal": "actor_action_budget", "max_actor_actions": 8,
                         "min_actions": 4}}
    # terminal declares the budget, the run stops at 4 -> not reached
    rules = scenario_a_ir()
    rules["terminal"] = {"max_actor_actions": 8}
    result = _run(case, rules, final={**FINAL_A, "action_count": 4})
    assert result["ok"] is False
    assert any(check["name"] == "reached.actions" and not check["ok"] for check in result["checks"])


def test_hidden_expectation_rejects_a_public_hand():
    rules = scenario_b_ir()
    rules["terminal"] = {"score_reaches": 6}
    result = _run(CASE_HIDDEN, rules, initial=_public_initial(), final=FINAL_HIDDEN)
    assert result["ok"] is False
    assert any(check["name"] == "hidden_projection" and not check["ok"]
               for check in result["checks"])


def test_hidden_case_passes_with_an_owner_only_hand():
    rules = scenario_b_ir()
    rules["terminal"] = {"score_reaches": 6}
    result = _run(CASE_HIDDEN, rules, initial=_hidden_initial(3), final=FINAL_HIDDEN)
    assert result["ok"] is True, [c for c in result["checks"] if not c["ok"]]


def test_a_player_count_mismatch_fails():
    case = {**CASE_A, "evidence": {**CASE_A["evidence"], "players": 3}}
    result = _run(case, scenario_a_ir())
    assert result["ok"] is False
    assert any(check["name"] == "players" and not check["ok"] for check in result["checks"])


def test_a_highest_score_case_checks_its_round_budget():
    case = {**CASE_A, "evidence": {**CASE_A["evidence"], "max_rounds": 4}}
    result = _run(case, scenario_a_ir())          # scenario A has max_rounds 3
    assert result["ok"] is False
    assert any(check["name"] == "terminal.max_rounds" and not check["ok"]
               for check in result["checks"])
