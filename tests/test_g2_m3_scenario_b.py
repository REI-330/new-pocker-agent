"""G2 / M3 scenario B: match, suit scoring, skip, draw/pass (ADR-0012).

A two-player match game with hidden hands and a shared discard pile:
play a same-rank/same-suit card (heart +2, spade +1), skip on a seven, draw one
when no card matches, and pass when the stock is empty. The terminal rule is a
score threshold with a 20-action budget -- emptying a hand never ends the game.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from pocker_agent.core import (
    Interpreter,
    ToolError,
    compile_composed,
    contract_check,
    core_registry,
    run_bots,
)
from pocker_agent.core.ir import parse_design_ir
from pocker_agent.core.plan import GamePlan
from pocker_agent.core.playtest import boundary_first
from pocker_agent.core.verify import (
    VERIFICATION_STRATEGIES,
    verify_composed,
)

# The formal gate's default seeds stop the game at the score threshold before the
# stock empties; seed 28 reaches the ``pass`` wait, so scenario B verifies against
# a seed set that covers all three candidate actions (B9).
SCENARIO_B_SEEDS = (0, 1, 7, 23, 28, 42)


def _top(zone: str) -> dict:
    return {"op": "top", "zone": zone, "field": "rank"}


def scenario_b_ir(*, hand_size: int = 4) -> dict:
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "接牌 + 花色计分 + 跳过", "description": "场景 B。"},
        "requirements": [],
        "players": {"count": 2},
        "deck": {"ranks": [str(n) for n in range(2, 10)], "suits": ["S", "H"],
                 "copies": 1, "values": {str(n): n for n in range(2, 10)}},
        "zones": [
            {"id": "hand", "visibility": "owner_only", "scope": "player"},
            {"id": "discard", "visibility": "public", "scope": "shared"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": hand_size, "per_seat": True},
                            {"zone": "discard", "count": 1, "per_seat": False}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [
            {"id": "play",
             "guard": {"op": "has_match", "zone": "hand", "top": _top("discard")},
             "inputs": [{"id": "card", "kind": "card_selection", "zone": "hand",
                         "scope": "actor", "min_count": 1, "max_count": 1}],
             "effects": [
                 {"kind": "select", "input": "card", "result": "picked",
                  "match_top": "discard"},
                 {"kind": "move", "from_zone": "hand", "to_zone": "discard",
                  "selection": "picked"},
                 {"kind": "score_top", "zone": "discard", "by": "suit",
                  "points": {"H": 2, "S": 1}},
             ],
             "trigger": [{"kind": "skip", "count": 1,
                          "condition": {"op": "eq", "left": _top("discard"),
                                        "right": {"op": "lit", "value": "7"}}}]},
            {"id": "draw",
             "guard": {"op": "all", "items": [
                 {"op": "not", "operand": {"op": "has_match", "zone": "hand",
                                           "top": _top("discard")}},
                 {"op": "gt", "left": {"op": "zone_count", "zone": "stock"},
                  "right": {"op": "lit", "value": 0}}]},
             "inputs": [],
             "effects": [{"kind": "draw", "from_zone": "stock", "to_zone": "hand",
                          "count": 1}]},
            {"id": "pass", "inputs": [], "effects": []},
        ],
        "flow": {"round_action": "play", "turn_actions": ["play", "draw", "pass"],
                 "start_seat": "seat0", "resolve": []},
        "scoring": [],
        "terminal": {"score_reaches": 6, "max_actor_actions": 20},
        "macros": [],
    }


def _started(payload: dict, seed: int = 0) -> Interpreter:
    compiled = compile_composed(parse_design_ir(payload), core_registry())
    interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
    interpreter.setup()
    return interpreter


def _card_matches(card, top) -> bool:
    return card.suit == top.suit or card.rank == top.rank


def mutate(plan: GamePlan, edit) -> GamePlan:
    data = copy.deepcopy(plan.model_dump(mode="json"))
    edit(data["nodes"])
    return GamePlan.model_validate(data)


def test_scenario_b_compiles_verifies_and_plays():
    ir = parse_design_ir(scenario_b_ir())
    compiled, result = verify_composed(ir, core_registry(), seeds=SCENARIO_B_SEEDS)
    assert result.ok, result.failures
    contract = contract_check(ir, compiled.plan, core_registry(),
                             VERIFICATION_STRATEGIES, SCENARIO_B_SEEDS)
    assert contract.ok, contract.failures()
    for seed in range(6):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter, human_index=-1)
        assert interpreter.state["finished"] is True
        total = sum(len(entry["cards"])
                    for entry in interpreter.state["zones"].values())
        assert total == 16                                   # cards conserved
        assert max(interpreter.state["scores"]) >= 1


def test_the_opening_card_does_not_score():
    interpreter = _started(scenario_b_ir())
    assert interpreter.state["scores"] == [0, 0]
    assert len(interpreter.state["zones"]["discard"]["cards"]) == 1


def test_an_illegal_play_is_rejected_and_does_not_change_the_board():
    interpreter = _started(scenario_b_ir())
    top = interpreter.state["zones"]["discard"]["cards"][-1]
    hand = interpreter.state["zones"]["hand-0"]["cards"]
    illegal = next((card.id for card in hand if not _card_matches(card, top)), None)
    if illegal is None:
        pytest.skip("this seed's hand has no non-matching card")
    before = copy.deepcopy(interpreter.state)
    with pytest.raises(ToolError, match="selection_does_not_match"):
        interpreter.step("play", card=[illegal])
    assert interpreter.state == before


def test_suit_scoring_matches_the_played_card():
    interpreter = _started(scenario_b_ir())
    while not interpreter.state.get("finished"):
        seat = interpreter.state["current_player"]
        top = interpreter.state["zones"]["discard"]["cards"][-1]
        scores = list(interpreter.state["scores"])
        actions = interpreter.legal_actions()
        if actions == ["play"]:
            hand = interpreter.state["zones"][f"hand-{seat}"]["cards"]
            card = next(card for card in hand if _card_matches(card, top))
            interpreter.step("play", card=[card.id])
            expected = 2 if card.suit == "H" else 1
            assert interpreter.state["scores"][seat] - scores[seat] == expected
        else:
            interpreter.step(actions[0])


def test_emptying_a_hand_does_not_end_the_game():
    for seed in range(24):
        interpreter = _started(scenario_b_ir(hand_size=1), seed=seed)
        if interpreter.legal_actions() != ["play"]:
            continue
        interpreter.step("play", card=[interpreter.state["zones"]["hand-0"]["cards"][0].id])
        assert interpreter.state["zones"]["hand-0"]["cards"] == []
        assert interpreter.state["finished"] is False       # B8: hand empty is not the end
        return
    pytest.fail("no seed dealt a single matching card")


def test_playing_a_seven_skips_the_next_seat():
    for seed in range(48):
        interpreter = _started(scenario_b_ir(), seed)
        if interpreter.legal_actions() != ["play"]:
            continue
        top = interpreter.state["zones"]["discard"]["cards"][-1]
        seven = next((card for card in interpreter.state["zones"]["hand-0"]["cards"]
                      if card.rank == "7" and _card_matches(card, top)), None)
        if seven is None:
            continue
        interpreter.step("play", card=[seven.id])
        assert interpreter.state["scores"][0] < 6
        assert interpreter.state["round"] == 2              # the next seat was skipped
        assert interpreter.state["action_count"] == 1
        return
    pytest.fail("no seed offered a matching seven")


def test_mutating_the_suit_points_is_rejected():
    ir = parse_design_ir(scenario_b_ir())
    compiled = compile_composed(ir, core_registry())

    def edit(nodes):
        for node in nodes.values():
            if (node.get("kind") == "call" and node["action"]["tool"] == "score_settle"
                    and node["action"]["args"].get("points") == 2):
                node["action"]["args"]["points"] = 5

    report = contract_check(ir, mutate(compiled.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert next(check for check in report.checks
                if check.name == "suit_scoring").ok is False


def test_score_top_requires_a_shared_zone_and_points():
    payload = scenario_b_ir()
    payload["actions"][0]["effects"][2]["zone"] = "hand"
    with pytest.raises(ValidationError, match="score_top_requires_shared_zone"):
        parse_design_ir(payload)
    payload = scenario_b_ir()
    payload["actions"][0]["effects"][2]["points"] = {}
    with pytest.raises(ValidationError, match="score_top_requires_points"):
        parse_design_ir(payload)


def test_select_matching_requires_a_shared_top_zone():
    payload = scenario_b_ir()
    payload["actions"][0]["effects"][0]["match_top"] = "hand"
    with pytest.raises(ValidationError, match="select_match_zone_must_be_shared"):
        parse_design_ir(payload)
