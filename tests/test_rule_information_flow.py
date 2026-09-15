from __future__ import annotations

import pytest
from test_g2_m2 import scenario_a_ir

from pocker_agent.core.ir import parse_design_ir


def test_a_guard_cannot_read_a_hidden_card_identity() -> None:
    payload = scenario_a_ir()
    payload["actions"][0]["guard"] = {
        "op": "eq",
        "left": {"op": "top", "zone": "stock", "field": "rank"},
        "right": {"op": "lit", "value": "A"},
    }
    with pytest.raises(ValueError, match="guard_reads_hidden_card_identity:play:stock"):
        parse_design_ir(payload)


def test_an_action_cannot_select_cards_from_a_hidden_shared_zone() -> None:
    payload = scenario_a_ir()
    payload["actions"][0]["inputs"][0].update({"zone": "stock", "scope": "shared"})
    with pytest.raises(ValueError, match="action_input_reads_hidden_zone:play:card"):
        parse_design_ir(payload)


def test_a_match_constraint_cannot_reveal_the_top_of_a_hidden_zone() -> None:
    payload = scenario_a_ir()
    payload["actions"][0]["effects"][0]["match_top"] = "stock"
    with pytest.raises(ValueError, match="select_match_zone_must_be_public:play:stock"):
        parse_design_ir(payload)
