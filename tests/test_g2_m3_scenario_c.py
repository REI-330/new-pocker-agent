"""G2 / M3 scenario C: generic duplicate removal + bounded refill (ADR-0011).

Two real consumers exercise the mechanism: the scenario-C pair game (group of 2,
+2 each) and a Go-Fish-style book collection (group of 4). The tests cover the
operation as a pure deterministic partition, the compiled rule end to end, the
independent pair/refill monitors, mutation rejection, and credential staleness
after a contract change.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from pocker_agent.core import (
    CardRef,
    Interpreter,
    SessionStore,
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
    VERIFICATION_SEEDS,
    VERIFICATION_STRATEGIES,
    verify_composed,
)
from pocker_agent.core.zones import duplicate_groups


# ------------------------------------------------------------------ rule data
def scenario_c_ir() -> dict:
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "公开市场交换 + 成对移除", "description": "场景 C 机制。"},
        "requirements": [],
        "players": {"count": 2},
        "deck": {"ranks": [str(n) for n in range(2, 10)], "suits": ["S", "H"],
                 "copies": 1, "values": {str(n): n for n in range(2, 10)}},
        "zones": [
            {"id": "hand", "visibility": "public", "scope": "player"},
            {"id": "market", "visibility": "public", "scope": "shared"},
            {"id": "collection", "visibility": "public", "scope": "player"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": 3, "per_seat": True},
                            {"zone": "market", "count": 3, "per_seat": False}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [{
            "id": "exchange",
            "inputs": [
                {"id": "hand_card", "kind": "card_selection", "zone": "hand",
                 "scope": "actor", "min_count": 1, "max_count": 1},
                {"id": "market_card", "kind": "card_selection", "zone": "market",
                 "scope": "shared", "min_count": 1, "max_count": 1},
            ],
            "effects": [
                {"kind": "select", "input": "hand_card", "result": "from_hand"},
                {"kind": "select", "input": "market_card", "result": "from_market"},
                {"kind": "move", "from_zone": "hand", "to_zone": "market",
                 "selection": "from_hand"},
                {"kind": "move", "from_zone": "market", "to_zone": "hand",
                 "selection": "from_market"},
                {"kind": "remove_pairs", "from_zone": "hand", "to_zone": "collection",
                 "group_size": 2, "points_per_pair": 2},
                {"kind": "refill", "from_zone": "stock", "to_zone": "hand",
                 "target_count": 3, "max_draw": 3},
            ],
        }],
        "flow": {"round_action": "exchange", "start_seat": "seat0", "resolve": []},
        "scoring": [],
        "terminal": {"max_actor_actions": 4},
        "macros": [],
    }


def book_collection_ir() -> dict:
    """The second consumer: collect four of a rank into a book."""
    return {
        "schema_version": "0.5", "kind": "composed",
        "meta": {"title": "成套收集（四张一组）", "description": "ADR-0011 第二消费者。"},
        "requirements": [],
        "players": {"count": 2},
        "deck": {"ranks": [str(n) for n in range(2, 7)], "suits": ["S", "H", "D", "C"],
                 "copies": 3, "values": {str(n): n for n in range(2, 7)}},
        "zones": [
            {"id": "hand", "visibility": "owner_only", "scope": "player"},
            {"id": "collection", "visibility": "public", "scope": "player"},
            {"id": "stock", "visibility": "hidden", "scope": "shared"},
        ],
        "setup": {"deals": [{"zone": "hand", "count": 5, "per_seat": True}],
                  "stock_zone": "stock"},
        "variables": [],
        "actions": [{
            "id": "collect",
            "inputs": [],
            "effects": [
                {"kind": "refill", "from_zone": "stock", "to_zone": "hand",
                 "target_count": 5, "max_draw": 5},
                {"kind": "remove_pairs", "from_zone": "hand", "to_zone": "collection",
                 "group_size": 4, "points_per_pair": 3},
            ],
        }],
        "flow": {"round_action": "collect", "start_seat": "seat0", "resolve": []},
        "scoring": [],
        "terminal": {"max_actor_actions": 6},
        "macros": [],
    }


def compile_ir(payload: dict):
    ir = parse_design_ir(payload)
    return ir, compile_composed(ir, core_registry())


def mutate(plan: GamePlan, edit):
    data = copy.deepcopy(plan.model_dump(mode="json"))
    edit(data["nodes"])
    return GamePlan.model_validate(data)


# ------------------------------------------------------------ the operation
def test_duplicate_groups_is_a_deterministic_disjoint_partition():
    zones = {"hand-0": {"cards": [
        CardRef("2S", "2", "S", 2), CardRef("2H", "2", "H", 2),
        CardRef("3S", "3", "S", 3), CardRef("3H", "3", "H", 3),
        CardRef("3D", "3", "D", 3), CardRef("5S", "5", "S", 5)]}}
    result = duplicate_groups(zones, "hand-0", "rank", min_count=2, max_group=2, max_total=8)
    assert [group["key"] for group in result["groups"]] == ["2", "3"]
    assert result["ids"] == ["2H", "2S", "3D", "3H"]        # value order, id-sorted
    flat = [card for group in result["groups"] for card in group["ids"]]
    assert sorted(flat) == sorted(result["ids"])            # a partition, no card twice
    assert len(set(result["ids"])) == len(result["ids"])


def test_duplicate_groups_supports_a_four_card_book():
    zones = {"hand-0": {"cards": [CardRef(f"2{suit}", "2", suit, 2) for suit in "SHDC"]
                                  + [CardRef("5S", "5", "S", 5)]}}
    result = duplicate_groups(zones, "hand-0", "rank", min_count=4, max_group=4, max_total=8)
    assert result["groups"] == [{"key": "2", "ids": ["2C", "2D", "2H", "2S"]}]
    assert result["count"] == 4


def test_select_duplicates_rejects_bad_keys_and_bounds():
    zones = {"hand-0": {"cards": [CardRef("2S", "2", "S", 2)]}}
    with pytest.raises(ToolError, match="duplicate_key_unsupported"):
        duplicate_groups(zones, "hand-0", "owner")
    with pytest.raises(ToolError, match="invalid_duplicate_bounds"):
        duplicate_groups(zones, "hand-0", "rank", min_count=3, max_group=2)


# --------------------------------------------------------- scenario C module
def test_scenario_c_compiles_verifies_and_plays():
    ir, rules = compile_ir(scenario_c_ir())
    compiled, result = verify_composed(ir, core_registry())
    assert result.ok, result.failures
    contract = contract_check(ir, rules.plan, core_registry(),
                              VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert contract.ok, contract.failures()
    for seed in range(6):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter, human_index=-1)
        zones = interpreter.state["zones"]
        assert interpreter.state["finished"] is True
        assert interpreter.state["action_count"] == 4
        assert len(zones["market"]["cards"]) == 3            # market stays three
        total = sum(len(entry["cards"]) for entry in zones.values())
        assert total == 16                                   # cards conserved
        for seat in range(2):
            assert len(zones[f"hand-{seat}"]["cards"]) == 3  # refilled to three
            collected = len(zones[f"collection-{seat}"]["cards"])
            assert interpreter.state["scores"][seat] == collected  # 2 per pair


def test_scenario_c_scores_two_per_removed_pair():
    ir, _ = compile_ir(scenario_c_ir())
    compiled, _ = verify_composed(ir, core_registry())
    scored = 0
    for seed in range(8):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter, human_index=-1)
        for seat in range(2):
            collected = len(interpreter.state["zones"][f"collection-{seat}"]["cards"])
            assert interpreter.state["scores"][seat] == collected
            scored += collected
    assert scored, "the deterministic seeds must remove at least one pair"


def test_mutating_points_per_pair_is_rejected():
    ir, rules = compile_ir(scenario_c_ir())

    def edit(nodes):
        nodes["act_exchange_4_pair_points"]["action"]["args"]["expression"]["mul"][1] = 5

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert next(check for check in report.checks if check.name == "pair_scoring").ok is False


def test_mutating_the_refill_target_is_rejected():
    ir, rules = compile_ir(scenario_c_ir())

    def edit(nodes):
        # draw to five instead of three: the refill monitor must catch the overfill
        nodes["act_exchange_5_refill_0_can"]["action"]["args"]["expression"]["all"][0]["lt"][1] = 5

    report = contract_check(ir, mutate(rules.plan, edit), core_registry(),
                            [boundary_first], (0,))
    assert report.ok is False
    assert next(check for check in report.checks if check.name == "refill").ok is False


# ------------------------------------------------------- second real consumer
def test_the_book_collection_consumer_uses_the_same_mechanism():
    ir, _ = compile_ir(book_collection_ir())
    compiled, result = verify_composed(ir, core_registry())
    assert result.ok, result.failures
    contract = contract_check(ir, compiled.plan, core_registry(),
                             VERIFICATION_STRATEGIES, VERIFICATION_SEEDS)
    assert contract.ok, contract.failures()
    (effect,) = [effect for action in ir.actions for effect in action.effects
                 if effect.kind == "remove_pairs"]
    assert effect.group_size == 4 and effect.points_per_pair == 3
    for seed in range(4):
        interpreter = Interpreter(compiled.plan, core_registry(), seed=seed)
        interpreter.setup()
        run_bots(interpreter, human_index=-1)
        zones = interpreter.state["zones"]
        assert interpreter.state["finished"] is True
        total = sum(len(entry["cards"]) for entry in zones.values())
        assert total == 60                                    # 5 ranks x 4 suits x 3 copies
        for seat in range(2):
            books = len(zones[f"collection-{seat}"]["cards"])
            assert interpreter.state["scores"][seat] == 3 * (books // 4)


# ------------------------------------------------------------ rule validation
def test_remove_pairs_requires_player_scoped_zones():
    payload = scenario_c_ir()
    payload["actions"][0]["effects"][4]["to_zone"] = "market"     # shared target
    with pytest.raises(ValidationError, match="remove_pairs_zone_scope_mismatch"):
        parse_design_ir(payload)


def test_refill_requires_a_shared_stock_and_a_player_target():
    payload = scenario_c_ir()
    payload["actions"][0]["effects"][5]["from_zone"] = "hand"
    with pytest.raises(ValidationError, match="refill_stock_must_be_shared"):
        parse_design_ir(payload)
    payload = scenario_c_ir()
    payload["actions"][0]["effects"][5]["target_count"] = 9
    with pytest.raises(ValidationError, match="refill_max_draw_below_target"):
        parse_design_ir(payload)


# ------------------------------------------------------------- credential gate
def test_a_contract_hash_change_invalidates_a_recorded_credential(tmp_path):
    from pocker_agent.core import bind_rules_game_id, normalize_rules, rules_fingerprint
    from pocker_agent.core.artifacts import GameArtifact

    store = SessionStore(tmp_path / "c.db")
    rules = bind_rules_game_id(normalize_rules(scenario_c_ir()), "scenario-c")
    store.verify_and_register_rules(
        rules, version=1, title="C", approval_rules_hash=rules_fingerprint(rules),
    )
    stored = store.list_versions("scenario-c")[0]
    stale = GameArtifact.from_dict({**stored, "version": 2,
                                    "registry_contract_hash": "changed"})
    with pytest.raises(ValueError, match="artifact_registry_contract_mismatch"):
        store.register_artifact(stale)
